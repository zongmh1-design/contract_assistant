import json

import httpx
import pytest

from app.integrations.llm import (
    LlmConfigurationError,
    LlmProviderSettings,
    OpenAICompatibleLLMProvider,
    create_llm_provider_from_environment,
)
from app.main import create_app
from app.schemas import LlmSemanticRuleResponse
from tests.test_contract_extraction import chinese_contract_blocks, create_task_with_snapshot


def test_empty_environment_disables_real_provider() -> None:
    assert create_llm_provider_from_environment({}) is None


def test_partial_environment_is_rejected_without_exposing_values() -> None:
    with pytest.raises(LlmConfigurationError) as captured:
        create_llm_provider_from_environment(
            {"LLM_BASE_URL": "https://llm.example/v1", "LLM_API_KEY": "secret"}
        )
    assert "LLM_MODEL" in str(captured.value)
    assert "secret" not in str(captured.value)


@pytest.mark.parametrize("timeout", ["invalid", "0", "-1"])
def test_timeout_must_be_positive_number(timeout: str) -> None:
    with pytest.raises(LlmConfigurationError):
        LlmProviderSettings.from_environment(
            {
                "LLM_BASE_URL": "https://llm.example/v1",
                "LLM_API_KEY": "secret",
                "LLM_MODEL": "test-model",
                "LLM_TIMEOUT_SECONDS": timeout,
            }
        )


def test_settings_create_provider_and_hide_api_key_from_repr() -> None:
    settings = LlmProviderSettings.from_environment(
        {
            "LLM_BASE_URL": "https://llm.example/v1/",
            "LLM_API_KEY": "secret-value",
            "LLM_MODEL": "test-model",
            "LLM_TIMEOUT_SECONDS": "12.5",
        }
    )
    assert settings is not None
    provider = settings.create_provider()
    assert provider.base_url == "https://llm.example/v1"
    assert provider.model_name == "test-model"
    assert provider.timeout_seconds == 12.5
    assert "secret-value" not in repr(settings)


def test_create_app_only_loads_environment_when_explicitly_enabled(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    test_app = create_app(database_url="sqlite://")
    formal_app = create_app(
        database_url="sqlite://", load_llm_from_environment=True
    )

    assert test_app.state.llm_provider is None
    assert formal_app.state.llm_provider.model_name == "test-model"


def test_provider_uses_requested_schema_name_and_records_usage() -> None:
    captured_body = {}

    def respond(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "decision": "not_hit",
                                    "reason": "未发现明确风险线索",
                                    "block_start": None,
                                    "block_end": None,
                                }
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 21,
                    "completion_tokens": 8,
                    "total_tokens": 29,
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    provider = OpenAICompatibleLLMProvider(
        api_key="test-key",
        model="test-model",
        base_url="https://llm.example/v1",
        client=client,
    )
    result = provider.generate_structured(
        system_prompt="semantic review",
        user_prompt="{}",
        response_schema=LlmSemanticRuleResponse,
    )
    client.close()

    schema_config = captured_body["response_format"]["json_schema"]
    assert schema_config["name"] == "LlmSemanticRuleResponse"
    assert schema_config["strict"] is True
    assert result.usage.total_tokens == 29


@pytest.mark.parametrize(
    ("failure_mode", "expected_error"),
    [
        ("timeout", "LlmProviderTimeoutError"),
        ("http_error", "LlmProviderError"),
    ],
)
def test_real_adapter_failure_degrades_business_result(
    client, failure_mode: str, expected_error: str
) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        if failure_mode == "timeout":
            raise httpx.ReadTimeout("controlled timeout")
        return httpx.Response(503, request=request, json={"error": "unavailable"})

    blocks = chinese_contract_blocks()
    blocks[1]["text"] = "项目代号为 SMOKE-2026-TIMEOUT。"
    task_id, _, _ = create_task_with_snapshot(client, blocks)
    transport_client = httpx.Client(transport=httpx.MockTransport(fail))
    client.app.state.llm_provider = OpenAICompatibleLLMProvider(
        api_key="test-key",
        model="test-model",
        base_url="https://llm.example/v1",
        timeout_seconds=0.1,
        client=transport_client,
    )

    response = client.post(f"/api/tasks/{task_id}/parse-contract/llm-assist")
    transport_client.close()

    assert response.status_code == 200
    result = response.json()
    assert result["parse_status"] == "partial"
    assert result["llm_metadata_json"]["degraded"] is True
    assert result["llm_metadata_json"]["error_type"] == expected_error
    assert client.get(f"/api/tasks/{task_id}").json()["task_status"] == "parsing"
