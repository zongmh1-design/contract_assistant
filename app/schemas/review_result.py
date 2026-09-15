from datetime import datetime, timezone

from pydantic import BaseModel, field_validator

from app.models import ReviewResult, ReviewStatus, RiskLevel


class ReviewResultRead(BaseModel):
    id: int
    contract_parse_id: int
    overall_risk_level: RiskLevel
    summary_text: str
    focus_points_json: list[dict]
    comment_text: str
    review_status: ReviewStatus
    review_error: str | None
    review_version: str
    rule_set_fingerprint: str
    rule_hit_fingerprint: str
    rule_hit_ids: list[int]
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def normalize_sqlite_datetime(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    @classmethod
    def from_model(cls, result: ReviewResult) -> "ReviewResultRead":
        return cls(
            id=result.id,
            contract_parse_id=result.contract_parse_id,
            overall_risk_level=result.overall_risk_level,
            summary_text=result.summary_text,
            focus_points_json=result.focus_points_json,
            comment_text=result.comment_text,
            review_status=result.review_status,
            review_error=result.review_error,
            review_version=result.review_version,
            rule_set_fingerprint=result.rule_set_fingerprint,
            rule_hit_fingerprint=result.rule_hit_fingerprint,
            rule_hit_ids=[hit.id for hit in result.rule_hits],
            created_at=result.created_at,
        )
