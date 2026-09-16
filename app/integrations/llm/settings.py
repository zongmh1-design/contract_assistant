from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import os

from app.integrations.llm.openai_compatible_provider import (
    OpenAICompatibleLLMProvider,
)
from app.integrations.llm.provider import LLMProvider


class LlmConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class LlmProviderSettings:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float = 30.0

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "LlmProviderSettings | None":
        values = environment if environment is not None else os.environ
        required_names = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")
        configured = {
            name: values.get(name, "").strip() for name in required_names
        }
        if not any(configured.values()):
            return None
        missing = [name for name, value in configured.items() if not value]
        if missing:
            raise LlmConfigurationError(
                "LLM 配置不完整，缺少环境变量: " + ", ".join(missing)
            )

        raw_timeout = values.get("LLM_TIMEOUT_SECONDS", "30").strip()
        try:
            timeout_seconds = float(raw_timeout)
        except ValueError as error:
            raise LlmConfigurationError(
                "LLM_TIMEOUT_SECONDS 必须是正数"
            ) from error
        if timeout_seconds <= 0:
            raise LlmConfigurationError("LLM_TIMEOUT_SECONDS 必须是正数")

        return cls(
            base_url=configured["LLM_BASE_URL"],
            api_key=configured["LLM_API_KEY"],
            model=configured["LLM_MODEL"],
            timeout_seconds=timeout_seconds,
        )

    def create_provider(self) -> OpenAICompatibleLLMProvider:
        return OpenAICompatibleLLMProvider(
            api_key=self.api_key,
            model=self.model,
            base_url=self.base_url,
            timeout_seconds=self.timeout_seconds,
        )


def create_llm_provider_from_environment(
    environment: Mapping[str, str] | None = None,
) -> LLMProvider | None:
    settings = LlmProviderSettings.from_environment(environment)
    return settings.create_provider() if settings is not None else None
