from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LlmRuleDecision(str, Enum):
    HIT = "hit"
    NOT_HIT = "not_hit"
    UNCERTAIN = "uncertain"


class LlmSemanticRuleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: LlmRuleDecision
    reason: str = Field(min_length=1)
    block_start: int | None = None
    block_end: int | None = None

    @model_validator(mode="after")
    def require_hit_evidence(self) -> "LlmSemanticRuleResponse":
        if self.decision == LlmRuleDecision.HIT and (
            self.block_start is None or self.block_end is None
        ):
            raise ValueError("hit 必须提供 block_start 和 block_end")
        return self
