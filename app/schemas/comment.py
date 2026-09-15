from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_validator

from app.models import WriteStatus
from app.schemas.task import ApprovalTaskRead


class CommentLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    review_result_id: int
    write_status: WriteStatus
    write_response_text: str | None
    external_comment_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def normalize_sqlite_datetime(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class CommentWritebackResponse(BaseModel):
    reused: bool
    task: ApprovalTaskRead
    comment_log: CommentLogRead
