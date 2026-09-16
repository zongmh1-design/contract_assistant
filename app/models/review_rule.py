from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, Enum as SqlEnum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.approval_task import enum_values, utc_now


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RuleStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class MatchMode(str, Enum):
    FIELD_MISSING = "field_missing"
    NUMERIC_THRESHOLD = "numeric_threshold"
    KEYWORD = "keyword"
    PRESENCE = "presence"
    FUTURE_LLM = "future_llm"
    LLM_SEMANTIC = "llm_semantic"


class ReviewRule(Base):
    """可配置的审查规则定义；执行代码不保存风险等级和建议文案。"""

    __tablename__ = "review_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    rule_name: Mapped[str] = mapped_column(String(200), nullable=False)
    risk_level: Mapped[RiskLevel] = mapped_column(
        SqlEnum(
            RiskLevel,
            values_callable=enum_values,
            native_enum=False,
            create_constraint=True,
            name="risk_level",
        ),
        nullable=False,
    )
    rule_status: Mapped[RuleStatus] = mapped_column(
        SqlEnum(
            RuleStatus,
            values_callable=enum_values,
            native_enum=False,
            create_constraint=True,
            name="review_rule_status",
        ),
        nullable=False,
    )
    match_mode: Mapped[MatchMode] = mapped_column(
        SqlEnum(
            MatchMode,
            values_callable=enum_values,
            native_enum=False,
            create_constraint=True,
            name="review_rule_match_mode",
        ),
        nullable=False,
    )
    match_text: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion_text: Mapped[str] = mapped_column(Text, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    hits: Mapped[list["RuleHit"]] = relationship(back_populates="rule")
