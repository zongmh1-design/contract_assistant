from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, Enum as SqlEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskStatus(str, Enum):
    PENDING = "pending"
    PARSING = "parsing"
    REVIEWING = "reviewing"
    BLOCKED = "blocked"
    DONE = "done"


class WriteStatus(str, Enum):
    NOT_WRITTEN = "not_written"
    WRITING = "writing"
    SUCCESS = "success"
    FAILED = "failed"


def enum_values(enum_class: type[Enum]) -> list[str]:
    return [item.value for item in enum_class]


class ApprovalTask(Base):
    __tablename__ = "approval_tasks"
    __table_args__ = (
        UniqueConstraint("instance_id", name="uq_approval_tasks_instance_id"),
        UniqueConstraint("approval_code", name="uq_approval_tasks_approval_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(100), nullable=False)
    approval_code: Mapped[str] = mapped_column(String(100), nullable=False)
    approval_title: Mapped[str] = mapped_column(String(255), nullable=False)
    applicant_name: Mapped[str] = mapped_column(String(100), nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    task_status: Mapped[TaskStatus] = mapped_column(
        SqlEnum(
            TaskStatus,
            values_callable=enum_values,
            native_enum=False,
            create_constraint=True,
            name="task_status",
        ),
        default=TaskStatus.PENDING,
        nullable=False,
    )
    write_status: Mapped[WriteStatus] = mapped_column(
        SqlEnum(
            WriteStatus,
            values_callable=enum_values,
            native_enum=False,
            create_constraint=True,
            name="write_status",
        ),
        default=WriteStatus.NOT_WRITTEN,
        nullable=False,
    )
    blocked_stage: Mapped[str | None] = mapped_column(String(50))
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    retry_target: Mapped[str | None] = mapped_column(String(50))
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    logs: Mapped[list[TaskLog]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="TaskLog.id"
    )
    attachments: Mapped[list[ApprovalAttachment]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="ApprovalAttachment.id",
    )
    comment_logs: Mapped[list[CommentLog]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="CommentLog.id",
    )


class TaskLog(Base):
    __tablename__ = "task_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("approval_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    log_level: Mapped[str] = mapped_column(String(20), nullable=False)
    log_type: Mapped[str] = mapped_column(String(50), nullable=False)
    log_content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    task: Mapped[ApprovalTask] = relationship(back_populates="logs")
