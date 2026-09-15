from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.approval_task import enum_values, utc_now
from app.models.review_rule import RiskLevel


review_result_rule_hits = Table(
    "review_result_rule_hits",
    Base.metadata,
    Column(
        "review_result_id",
        ForeignKey("review_results.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "rule_hit_id",
        ForeignKey("rule_hits.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
)


class ReviewStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ReviewResult(Base):
    """某份 ContractParse 在明确规则集合和汇总版本下的不可变审查快照。"""

    __tablename__ = "review_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contract_parse_id: Mapped[int] = mapped_column(
        ForeignKey("contract_parses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    overall_risk_level: Mapped[RiskLevel] = mapped_column(
        SqlEnum(
            RiskLevel,
            values_callable=enum_values,
            native_enum=False,
            create_constraint=True,
            name="review_result_risk_level",
        ),
        nullable=False,
    )
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    focus_points_json: Mapped[list[dict]] = mapped_column(
        JSON, nullable=False, default=list
    )
    comment_text: Mapped[str] = mapped_column(Text, nullable=False)
    review_status: Mapped[ReviewStatus] = mapped_column(
        SqlEnum(
            ReviewStatus,
            values_callable=enum_values,
            native_enum=False,
            create_constraint=True,
            name="review_status",
        ),
        nullable=False,
    )
    review_error: Mapped[str | None] = mapped_column(Text)
    review_version: Mapped[str] = mapped_column(String(50), nullable=False)
    rule_set_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_hit_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    contract_parse: Mapped["ContractParse"] = relationship(
        back_populates="review_results"
    )
    rule_hits: Mapped[list["RuleHit"]] = relationship(
        secondary=review_result_rule_hits,
        back_populates="review_results",
        order_by="RuleHit.id",
    )
    comment_logs: Mapped[list["CommentLog"]] = relationship(
        back_populates="review_result",
        order_by="CommentLog.id",
    )
