from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.approval_task import utc_now


class LlmRuleEvaluation(Base):
    """一次语义规则判断审计记录；未命中、无法判断与降级同样可追溯。"""

    __tablename__ = "llm_rule_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contract_parse_id: Mapped[int] = mapped_column(
        ForeignKey("contract_parses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("review_rules.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    rule_version: Mapped[str] = mapped_column(String(50), nullable=False)
    evaluation_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    decision: Mapped[str | None] = mapped_column(String(20))
    evaluation_status: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    evidence_position: Mapped[dict | None] = mapped_column(JSON)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    rule_hit_id: Mapped[int | None] = mapped_column(
        ForeignKey("rule_hits.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    contract_parse: Mapped["ContractParse"] = relationship()
    rule: Mapped["ReviewRule"] = relationship()
    rule_hit: Mapped["RuleHit | None"] = relationship()
