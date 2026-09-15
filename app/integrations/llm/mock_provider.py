from __future__ import annotations

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


class MockLLMProvider(LLMProvider):
    def __init__(
        self,
        response: object,
        *,
        provider_name: str = "mock",
        model_name: str = "mock-contract-model",
        failure_mode: str | None = None,
        usage: LlmUsage | None = None,
    ) -> None:
        self.response = response
        self._provider_name = provider_name
        self._model_name = model_name
        self.failure_mode = failure_mode
        self.usage = usage or LlmUsage(100, 30, 130)
        self.calls = 0
        self.last_system_prompt: str | None = None
        self.last_user_prompt: str | None = None

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
        self.calls += 1
        self.last_system_prompt = system_prompt
        self.last_user_prompt = user_prompt
        if self.failure_mode == "timeout":
            raise LlmProviderTimeoutError("Mock LLM timeout")
        if self.failure_mode == "provider_error":
            raise LlmProviderError("Mock LLM provider error")
        if self.failure_mode == "invalid_schema":
            raise LlmResponseValidationError("Mock LLM schema invalid")
        if self.failure_mode == "invalid_json":
            raise LlmResponseValidationError("Mock LLM JSON invalid")
        try:
            output = response_schema.model_validate(self.response)
        except ValidationError as error:
            raise LlmResponseValidationError(f"Mock LLM schema invalid: {error}") from error
        return StructuredGeneration(
            output=output,
            provider=self.provider_name,
            model=self.model_name,
            usage=self.usage,
        )
