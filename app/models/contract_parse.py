from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, DateTime, Enum as SqlEnum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.approval_task import enum_values, utc_now


class ContractParseStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class ContractParse(Base):
    """某一读取快照经过指定版本 Extractor 后的结构化事实。"""

    __tablename__ = "contract_parses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_read_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("document_read_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    basic_info_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    clause_info_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    parse_status: Mapped[ContractParseStatus] = mapped_column(
        SqlEnum(
            ContractParseStatus,
            values_callable=enum_values,
            native_enum=False,
            create_constraint=True,
            name="contract_parse_status",
        ),
        nullable=False,
    )
    parse_error: Mapped[str | None] = mapped_column(Text)
    extractor_name: Mapped[str] = mapped_column(String(100), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(50), nullable=False)
    llm_metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    document_read_snapshot: Mapped["DocumentReadSnapshot"] = relationship(
        back_populates="contract_parses"
    )
    rule_hits: Mapped[list["RuleHit"]] = relationship(
        back_populates="contract_parse",
        cascade="all, delete-orphan",
        order_by="RuleHit.id",
    )
    review_results: Mapped[list["ReviewResult"]] = relationship(
        back_populates="contract_parse",
        cascade="all, delete-orphan",
        order_by="ReviewResult.id",
    )
