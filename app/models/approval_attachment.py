from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, Enum as SqlEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.approval_task import ApprovalTask, enum_values, utc_now


class DownloadStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class ApprovalAttachment(Base):
    __tablename__ = "approval_attachments"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "external_attachment_id",
            name="uq_approval_attachments_task_external_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("approval_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_attachment_id: Mapped[str] = mapped_column(String(100), nullable=False)
    original_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(1000))
    file_size: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    is_main_contract: Mapped[bool] = mapped_column(default=True, nullable=False)
    download_status: Mapped[DownloadStatus] = mapped_column(
        SqlEnum(
            DownloadStatus,
            values_callable=enum_values,
            native_enum=False,
            create_constraint=True,
            name="download_status",
        ),
        default=DownloadStatus.PENDING,
        nullable=False,
    )
    download_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    task: Mapped[ApprovalTask] = relationship(back_populates="attachments")
    document_reads: Mapped[list["DocumentReadSnapshot"]] = relationship(
        back_populates="attachment",
        cascade="all, delete-orphan",
        order_by="DocumentReadSnapshot.id",
    )
