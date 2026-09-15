from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import DocumentReadStatus


class DocumentTextBlock(BaseModel):
    text: str
    page_number: int | None
    block_index: int


class DocumentReadResult(BaseModel):
    document_id: int | None
    file_type: str
    text: str
    blocks: list[DocumentTextBlock]
    page_count: int | None
    requires_ocr: bool
    read_status: DocumentReadStatus
    error_code: str | None
    error_message: str | None


class DocumentReadSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    attachment_id: int
    read_method: str
    file_sha256: str
    file_type: str
    text: str
    blocks: list[DocumentTextBlock]
    page_count: int | None
    requires_ocr: bool
    read_status: DocumentReadStatus
    error_code: str | None
    error_message: str | None
    reader_version: str
    created_at: datetime
