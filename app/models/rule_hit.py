from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, DateTime, Enum as SqlEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.approval_task import enum_values, utc_now


class EvidenceType(str, Enum):
    SOURCE = "source"
    MISSING = "missing"
    DERIVED = "derived"


class RuleHitStatus(str, Enum):
    HIT = "hit"


class RuleHit(Base):
    """某个不可变 ContractParse 对某一规则版本的确定性命中。"""

    __tablename__ = "rule_hits"
    __table_args__ = (
        UniqueConstraint(
            "contract_parse_id",
            "rule_id",
            "rule_version",
            name="uq_rule_hits_parse_rule_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contract_parse_id: Mapped[int] = mapped_column(
        ForeignKey("contract_parses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("review_rules.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    rule_version: Mapped[str] = mapped_column(String(50), nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_position: Mapped[dict | None] = mapped_column(JSON)
    evidence_type: Mapped[EvidenceType] = mapped_column(
        SqlEnum(
            EvidenceType,
            values_callable=enum_values,
            native_enum=False,
            create_constraint=True,
            name="rule_hit_evidence_type",
        ),
        nullable=False,
    )
    actual_value: Mapped[str | None] = mapped_column(Text)
    expected_value: Mapped[str | None] = mapped_column(Text)
    hit_message: Mapped[str] = mapped_column(Text, nullable=False)
    hit_status: Mapped[RuleHitStatus] = mapped_column(
        SqlEnum(
            RuleHitStatus,
            values_callable=enum_values,
            native_enum=False,
            create_constraint=True,
            name="rule_hit_status",
        ),
        nullable=False,
        default=RuleHitStatus.HIT,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    contract_parse: Mapped["ContractParse"] = relationship(back_populates="rule_hits")
    rule: Mapped["ReviewRule"] = relationship(back_populates="hits")
    review_results: Mapped[list["ReviewResult"]] = relationship(
        secondary="review_result_rule_hits",
        back_populates="rule_hits",
    )
