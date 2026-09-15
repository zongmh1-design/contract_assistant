from enum import Enum

from pydantic import BaseModel


class DocumentReadStatus(str, Enum):
    SUCCESS = "success"
    OCR_REQUIRED = "ocr_required"
    FAILED = "failed"


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
