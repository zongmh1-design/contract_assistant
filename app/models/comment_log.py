from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum as SqlEnum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.approval_task import WriteStatus, enum_values, utc_now


class CommentLog(Base):
    """一次审批评论回写尝试；评论正文由 ReviewResult 保存。"""

    __tablename__ = "comment_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("approval_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    review_result_id: Mapped[int] = mapped_column(
        ForeignKey("review_results.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    write_status: Mapped[WriteStatus] = mapped_column(
        SqlEnum(
            WriteStatus,
            values_callable=enum_values,
            native_enum=False,
            create_constraint=True,
            name="comment_log_write_status",
        ),
        nullable=False,
    )
    write_response_text: Mapped[str | None] = mapped_column(Text)
    external_comment_id: Mapped[str | None] = mapped_column(String(200))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    task: Mapped["ApprovalTask"] = relationship(back_populates="comment_logs")
    review_result: Mapped["ReviewResult"] = relationship(back_populates="comment_logs")
