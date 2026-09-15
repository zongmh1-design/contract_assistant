from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import ValidationError

from app.integrations.llm.provider import (
    LLMProvider,
    LlmProviderError,
    LlmProviderTimeoutError,
    LlmResponseValidationError,
    LlmUsage,
    StructuredGeneration,
    StructuredOutput,
)


class OpenAICompatibleLLMProvider(LLMProvider):
    """调用支持 Chat Completions JSON Schema 的兼容服务。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float = 30.0,
        provider_name: str = "openai_compatible",
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self._model_name = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._provider_name = provider_name
        self.client = client

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[StructuredOutput],
    ) -> StructuredGeneration[StructuredOutput]:
        request_body = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "contract_extraction",
                    "strict": True,
                    "schema": response_schema.model_json_schema(),
                },
            },
        }
        try:
            if self.client is not None:
                response = self.client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=request_body,
                    timeout=self.timeout_seconds,
                )
            else:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=request_body,
                    )
            response.raise_for_status()
        except httpx.TimeoutException as error:
            raise LlmProviderTimeoutError(f"LLM 请求超时: {error}") from error
        except httpx.HTTPError as error:
            raise LlmProviderError(f"LLM HTTP 调用失败: {error}") from error

        try:
            payload: dict[str, Any] = response.json()
            content = payload["choices"][0]["message"]["content"]
            parsed = json.loads(content) if isinstance(content, str) else content
            output = response_schema.model_validate(parsed)
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as error:
            raise LlmResponseValidationError(f"LLM 结构化响应无效: {error}") from error

        usage_data = payload.get("usage") or {}
        return StructuredGeneration(
            output=output,
            provider=self.provider_name,
            model=self.model_name,
            usage=LlmUsage(
                prompt_tokens=_optional_int(usage_data.get("prompt_tokens")),
                completion_tokens=_optional_int(usage_data.get("completion_tokens")),
                total_tokens=_optional_int(usage_data.get("total_tokens")),
            ),
        )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None
