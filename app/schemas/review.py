from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_validator

from app.models import (
    EvidenceType,
    MatchMode,
    ReviewRule,
    RiskLevel,
    RuleHit,
    RuleHitStatus,
    RuleStatus,
)


class ReviewRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_code: str
    rule_name: str
    risk_level: RiskLevel
    rule_status: RuleStatus
    match_mode: MatchMode
    match_text: str
    suggestion_text: str
    rule_version: str
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def normalize_sqlite_datetime(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class RuleHitRead(BaseModel):
    id: int
    contract_parse_id: int
    rule_id: int
    rule_code: str
    rule_name: str
    risk_level: RiskLevel
    suggestion_text: str
    rule_version: str
    evidence_text: str
    evidence_position: dict | None
    evidence_type: EvidenceType
    actual_value: str | None
    expected_value: str | None
    hit_message: str
    hit_status: RuleHitStatus
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def normalize_sqlite_datetime(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    @classmethod
    def from_models(cls, hit: RuleHit, rule: ReviewRule) -> "RuleHitRead":
        return cls(
            id=hit.id,
            contract_parse_id=hit.contract_parse_id,
            rule_id=hit.rule_id,
            rule_code=rule.rule_code,
            rule_name=rule.rule_name,
            risk_level=rule.risk_level,
            suggestion_text=rule.suggestion_text,
            rule_version=hit.rule_version,
            evidence_text=hit.evidence_text,
            evidence_position=hit.evidence_position,
            evidence_type=hit.evidence_type,
            actual_value=hit.actual_value,
            expected_value=hit.expected_value,
            hit_message=hit.hit_message,
            hit_status=hit.hit_status,
            created_at=hit.created_at,
        )


class RuleReviewSummary(BaseModel):
    overall_risk_level: RiskLevel
    hit_count: int
    high_count: int
    medium_count: int
    low_count: int
    focus_points: list[str]


class RuleReviewResponse(BaseModel):
    contract_parse_id: int
    created_hit_count: int
    reused_hit_count: int
    skipped_future_llm_count: int
    summary: RuleReviewSummary
    hits: list[RuleHitRead]
