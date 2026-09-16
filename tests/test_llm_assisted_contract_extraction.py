import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from app.integrations.llm import (
    LlmProviderTimeoutError,
    MockLLMProvider,
    OpenAICompatibleLLMProvider,
)
from app.models import (
    ApprovalAttachment,
    DocumentReadSnapshot,
    DocumentReadStatus,
    DownloadStatus,
)
from scripts.run_contract_demo import upgrade_database


def _blocks(*, ambiguous_number: bool = False) -> list[dict]:
    texts = [
        "设备采购合同",
        "项目备案号 LLM-DEMO-009",
        "甲方：星海创新科技有限公司（虚构）",
        "乙方：远山设备供应有限公司（虚构）",
        "合同总金额为人民币500000元整",
        "生效日期：2026年10月1日",
        "到期日期：2027年9月30日",
        "付款条款：收到发票后30天内支付。",
    ]
    if ambiguous_number:
        texts[1:2] = ["合同编号：OLD-001", "合同编号：NEW-002"]
    return [
        {"text": text, "page_number": 1 if index < 5 else 2, "block_index": index}
        for index, text in enumerate(texts)
    ]


def _create_task_with_snapshot(client: TestClient, blocks: list[dict] | None = None) -> int:
    task = client.post("/api/tasks/sync", params={"limit": 1}).json()["tasks"][0]
    client.post(f"/api/tasks/{task['id']}/start-parsing")
    content_blocks = blocks or _blocks()
    with client.app.state.session_factory() as session:
        attachment = ApprovalAttachment(
            task_id=task["id"],
            external_attachment_id="llm-test-attachment",
            original_file_name="LLM测试合同.pdf",
            file_type="pdf",
            file_path="tests/fixtures/llm-test.pdf",
            file_size=100,
            sha256="d" * 64,
            is_main_contract=True,
            download_status=DownloadStatus.SUCCESS,
        )
        session.add(attachment)
        session.flush()
        session.add(
            DocumentReadSnapshot(
                attachment_id=attachment.id,
                read_method="pdf_text",
                file_sha256="d" * 64,
                file_type="pdf",
                text="\n".join(block["text"] for block in content_blocks),
                blocks_json=content_blocks,
                page_count=2,
                requires_ocr=False,
                read_status=DocumentReadStatus.SUCCESS,
                reader_version="llm-test-reader",
            )
        )
        session.commit()
    return task["id"]


def _provider(items: list[dict], **kwargs) -> MockLLMProvider:
    return MockLLMProvider({"items": items}, **kwargs)


def _enhance(client: TestClient, task_id: int, provider: MockLLMProvider):
    client.app.state.llm_provider = provider
    response = client.post(f"/api/tasks/{task_id}/parse-contract/llm-assist")
    assert response.status_code == 200, response.text
    return response.json()


def test_only_unresolved_fields_are_requested_and_deterministic_found_wins(
    client: TestClient,
) -> None:
    task_id = _create_task_with_snapshot(client)
    provider = _provider(
        [
            {
                "field_name": "contract_number",
                "value": "LLM-DEMO-009",
                "extract_status": "found",
                "block_start": 1,
                "block_end": 1,
            },
            {
                "field_name": "amount",
                "value": "1000000",
                "extract_status": "found",
                "block_start": 4,
                "block_end": 4,
            },
        ]
    )

    result = _enhance(client, task_id, provider)

    prompt = json.loads(provider.last_user_prompt or "{}")
    assert "contract_number" in prompt["target_fields"]
    assert "amount" not in prompt["target_fields"]
    assert result["basic_info_json"]["amount"]["value"] == "500000"
    assert result["basic_info_json"]["contract_number"]["value"] == "LLM-DEMO-009"
    assert result["llm_metadata_json"]["conflicts"][0]["field_name"] == "amount"
    logs = client.get(f"/api/tasks/{task_id}/logs").json()
    assert any(log["log_type"] == "LLM_EXTRACTION_CONFLICT" for log in logs)


def test_llm_evidence_and_page_are_rebuilt_from_document_blocks(
    client: TestClient,
) -> None:
    task_id = _create_task_with_snapshot(client)
    result = _enhance(
        client,
        task_id,
        _provider(
            [
                {
                    "field_name": "contract_number",
                    "value": "LLM-DEMO-009",
                    "extract_status": "found",
                    "block_start": 1,
                    "block_end": 1,
                }
            ]
        ),
    )
    fact = result["basic_info_json"]["contract_number"]
    assert fact["source_text"] == "项目备案号 LLM-DEMO-009"
    assert fact["position"] == {
        "block_start": 1,
        "block_end": 1,
        "page_start": 1,
        "page_end": 1,
    }
    assert fact["extract_method"] == "llm_verified"


def test_deterministic_ambiguous_field_can_be_resolved(client: TestClient) -> None:
    task_id = _create_task_with_snapshot(client, _blocks(ambiguous_number=True))
    result = _enhance(
        client,
        task_id,
        _provider(
            [
                {
                    "field_name": "contract_number",
                    "value": "NEW-002",
                    "extract_status": "found",
                    "block_start": 2,
                    "block_end": 2,
                }
            ]
        ),
    )
    assert result["basic_info_json"]["contract_number"]["value"] == "NEW-002"
    assert result["basic_info_json"]["contract_number"]["extract_status"] == "found"


@pytest.mark.parametrize(
    ("candidate", "expected_status", "reason"),
    [
        (
            {
                "field_name": "contract_number",
                "value": "LLM-DEMO-009",
                "extract_status": "found",
                "block_start": 99,
                "block_end": 99,
            },
            "failed",
            "INVALID_BLOCK_RANGE",
        ),
        (
            {
                "field_name": "contract_number",
                "value": "HALLUCINATED-999",
                "extract_status": "found",
                "block_start": 1,
                "block_end": 1,
            },
            "ambiguous",
            "VALUE_NOT_IN_EVIDENCE",
        ),
    ],
)
def test_invalid_evidence_cannot_be_saved_as_found(
    client: TestClient, candidate: dict, expected_status: str, reason: str
) -> None:
    task_id = _create_task_with_snapshot(client)
    result = _enhance(client, task_id, _provider([candidate]))
    fact = result["basic_info_json"]["contract_number"]
    assert fact["value"] is None
    assert fact["extract_status"] == expected_status
    assert result["llm_metadata_json"]["validation_errors"][0]["reason"] == reason


def test_llm_not_found_and_ambiguous_are_preserved_without_invention(
    client: TestClient,
) -> None:
    task_id = _create_task_with_snapshot(client)
    result = _enhance(
        client,
        task_id,
        _provider(
            [
                {
                    "field_name": "delivery_clause",
                    "value": None,
                    "extract_status": "not_found",
                },
                {
                    "field_name": "confidentiality_clause",
                    "value": None,
                    "extract_status": "ambiguous",
                    "block_start": 7,
                    "block_end": 7,
                },
            ]
        ),
    )
    assert result["clause_info_json"]["delivery_clause"]["extract_status"] == "not_found"
    confidentiality = result["clause_info_json"]["confidentiality_clause"]
    assert confidentiality["extract_status"] == "ambiguous"
    assert confidentiality["source_text"] == "付款条款：收到发票后30天内支付。"


@pytest.mark.parametrize(
    "failure_mode", ["timeout", "provider_error", "invalid_json", "invalid_schema"]
)
def test_llm_failure_degrades_without_blocking(
    client: TestClient, failure_mode: str
) -> None:
    task_id = _create_task_with_snapshot(client)
    result = _enhance(
        client,
        task_id,
        MockLLMProvider({}, failure_mode=failure_mode),
    )
    assert result["extractor_name"] == "hybrid_contract_extractor"
    assert result["llm_metadata_json"]["status"] == "degraded"
    assert result["llm_metadata_json"]["degraded"] is True
    assert result["parse_status"] == "partial"
    assert result["basic_info_json"]["amount"]["value"] == "500000"
    assert client.get(f"/api/tasks/{task_id}").json()["task_status"] == "parsing"
    logs = client.get(f"/api/tasks/{task_id}/logs").json()
    assert any(log["log_type"] == "LLM_EXTRACTION_DEGRADED" for log in logs)


def test_hybrid_parse_preserves_deterministic_history_and_reuses_version(
    client: TestClient,
) -> None:
    task_id = _create_task_with_snapshot(client)
    provider = _provider([])
    first = _enhance(client, task_id, provider)
    second = _enhance(client, task_id, provider)

    parses = client.get(f"/api/tasks/{task_id}/contract-parses").json()
    assert [item["extractor_name"] for item in parses] == [
        "deterministic_contract_extractor",
        "hybrid_contract_extractor",
    ]
    assert second["id"] == first["id"]
    assert provider.calls == 1


def test_degraded_parse_is_kept_but_does_not_prevent_retry(
    client: TestClient,
) -> None:
    task_id = _create_task_with_snapshot(client)
    failed_provider = MockLLMProvider({}, failure_mode="timeout")
    degraded = _enhance(client, task_id, failed_provider)
    recovered_provider = _provider(
        [
            {
                "field_name": "contract_number",
                "value": "LLM-DEMO-009",
                "extract_status": "found",
                "block_start": 1,
                "block_end": 1,
            }
        ]
    )
    recovered = _enhance(client, task_id, recovered_provider)

    assert degraded["llm_metadata_json"]["status"] == "degraded"
    assert recovered["id"] != degraded["id"]
    assert recovered["basic_info_json"]["contract_number"]["extract_status"] == "found"
    assert recovered_provider.calls == 1
    parses = client.get(f"/api/tasks/{task_id}/contract-parses").json()
    assert len(parses) == 3


def test_provider_or_model_change_creates_new_hybrid_parse(client: TestClient) -> None:
    task_id = _create_task_with_snapshot(client)
    first = _enhance(client, task_id, _provider([], model_name="model-a"))
    second = _enhance(client, task_id, _provider([], model_name="model-b"))
    assert second["id"] != first["id"]
    assert second["extractor_version"] != first["extractor_version"]
    assert len(client.get(f"/api/tasks/{task_id}/contract-parses").json()) == 3


def test_missing_provider_is_rejected_without_blocking(client: TestClient) -> None:
    task_id = _create_task_with_snapshot(client)
    response = client.post(f"/api/tasks/{task_id}/parse-contract/llm-assist")
    assert response.status_code == 409
    assert response.json()["detail"] == "LLM_PROVIDER_NOT_CONFIGURED"
    assert client.get(f"/api/tasks/{task_id}").json()["task_status"] == "parsing"


def test_second_migration_adds_llm_metadata_column(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'llm-migration.db').as_posix()}"
    upgrade_database(database_url)
    sqlalchemy_engine = create_engine(database_url)
    columns = {item["name"] for item in inspect(sqlalchemy_engine).get_columns("contract_parses")}
    assert "llm_metadata_json" in columns
    sqlalchemy_engine.dispose()


def test_openai_compatible_provider_validates_schema_and_usage() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "items": [
                                        {
                                            "field_name": "contract_number",
                                            "value": None,
                                            "extract_status": "not_found",
                                            "block_start": None,
                                            "block_end": None,
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    provider = OpenAICompatibleLLMProvider(
        api_key="test-key",
        model="test-model",
        base_url="https://llm.example/v1",
        client=client,
    )
    from app.schemas import LlmExtractionResponse

    result = provider.generate_structured(
        system_prompt="extract only",
        user_prompt="{}",
        response_schema=LlmExtractionResponse,
    )
    assert result.output.items[0].extract_status == "not_found"
    assert result.usage.total_tokens == 14
    client.close()


def test_openai_compatible_provider_maps_timeout() -> None:
    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    client = httpx.Client(transport=httpx.MockTransport(timeout))
    provider = OpenAICompatibleLLMProvider(
        api_key="test-key",
        model="test-model",
        base_url="https://llm.example/v1",
        client=client,
    )
    from app.schemas import LlmExtractionResponse

    with pytest.raises(LlmProviderTimeoutError):
        provider.generate_structured(
            system_prompt="extract only",
            user_prompt="{}",
            response_schema=LlmExtractionResponse,
        )
    client.close()
