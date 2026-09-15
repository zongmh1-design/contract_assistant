from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, Boolean, DateTime, Enum as SqlEnum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.approval_task import enum_values, utc_now


class DocumentReadStatus(str, Enum):
    SUCCESS = "success"
    OCR_REQUIRED = "ocr_required"
    FAILED = "failed"


class DocumentReadSnapshot(Base):
    """一次附件读取结果；旧快照保留，供后续阶段复用和审计。"""

    __tablename__ = "document_read_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attachment_id: Mapped[int] = mapped_column(
        ForeignKey("approval_attachments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    read_method: Mapped[str] = mapped_column(String(50), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    blocks_json: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    page_count: Mapped[int | None] = mapped_column(Integer)
    requires_ocr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    read_status: Mapped[DocumentReadStatus] = mapped_column(
        SqlEnum(
            DocumentReadStatus,
            values_callable=enum_values,
            native_enum=False,
            create_constraint=True,
            name="document_read_status",
        ),
        nullable=False,
    )
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    reader_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    attachment: Mapped["ApprovalAttachment"] = relationship(
        back_populates="document_reads"
    )
    contract_parses: Mapped[list["ContractParse"]] = relationship(
        back_populates="document_read_snapshot",
        cascade="all, delete-orphan",
        order_by="ContractParse.id",
    )

    @property
    def blocks(self) -> list[dict]:
        """API 使用业务名 blocks，数据库列保留 blocks_json 以明确存储形式。"""

        return self.blocks_json
