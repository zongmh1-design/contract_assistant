from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator


class LlmExtractStatus(str, Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"


class LlmFieldCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    value: str | None
    extract_status: LlmExtractStatus
    block_start: int | None = None
    block_end: int | None = None

    @model_validator(mode="after")
    def require_found_evidence_range(self) -> "LlmFieldCandidate":
        if self.extract_status == LlmExtractStatus.FOUND:
            if not self.value or self.block_start is None or self.block_end is None:
                raise ValueError("found 项必须包含 value、block_start 和 block_end")
        return self


class LlmExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[LlmFieldCandidate]
