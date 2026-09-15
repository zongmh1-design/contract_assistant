from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import DownloadStatus, TaskStatus, WriteStatus


class ApprovalTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    instance_id: str
    approval_code: str
    approval_title: str
    applicant_name: str
    applied_at: datetime | None
    task_status: TaskStatus
    write_status: WriteStatus
    blocked_stage: str | None
    blocked_reason: str | None
    retry_target: str | None
    retry_count: int
    created_at: datetime
    updated_at: datetime


class TaskLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    log_level: str
    log_type: str
    log_content: str
    created_at: datetime


class SyncTasksResponse(BaseModel):
    created_count: int
    updated_count: int
    tasks: list[ApprovalTaskRead]


class ApprovalAttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    external_attachment_id: str
    original_file_name: str
    file_type: str
    file_path: str | None
    file_size: int | None
    sha256: str | None
    is_main_contract: bool
    download_status: DownloadStatus
    download_error: str | None
    created_at: datetime
    updated_at: datetime


class AttachmentPreparationResponse(BaseModel):
    event: str
    task: ApprovalTaskRead
    attachment: ApprovalAttachmentRead | None
