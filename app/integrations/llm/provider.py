from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel


StructuredOutput = TypeVar("StructuredOutput", bound=BaseModel)


class LlmProviderError(RuntimeError):
    pass


class LlmProviderTimeoutError(LlmProviderError):
    pass


class LlmResponseValidationError(LlmProviderError):
    pass


@dataclass(frozen=True)
class LlmUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class StructuredGeneration(Generic[StructuredOutput]):
    output: StructuredOutput
    provider: str
    model: str
    usage: LlmUsage


class LLMProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[StructuredOutput],
    ) -> StructuredGeneration[StructuredOutput]: ...
