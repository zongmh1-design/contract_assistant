from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ApprovalAttachment,
    DocumentReadSnapshot,
    DocumentReadStatus,
)


class DocumentReadRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_reusable_success(
        self,
        attachment_id: int,
        file_sha256: str,
        reader_version: str,
    ) -> DocumentReadSnapshot | None:
        statement = (
            select(DocumentReadSnapshot)
            .where(
                DocumentReadSnapshot.attachment_id == attachment_id,
                DocumentReadSnapshot.file_sha256 == file_sha256,
                DocumentReadSnapshot.reader_version == reader_version,
                DocumentReadSnapshot.read_status == DocumentReadStatus.SUCCESS,
            )
            .order_by(DocumentReadSnapshot.id.desc())
        )
        return self.session.scalar(statement)

    def find_reusable_ocr_success(
        self,
        attachment_id: int,
        file_sha256: str,
        ocr_version: str,
    ) -> DocumentReadSnapshot | None:
        statement = (
            select(DocumentReadSnapshot)
            .where(
                DocumentReadSnapshot.attachment_id == attachment_id,
                DocumentReadSnapshot.file_sha256 == file_sha256,
                DocumentReadSnapshot.read_method == "ocr",
                DocumentReadSnapshot.reader_version == ocr_version,
                DocumentReadSnapshot.read_status == DocumentReadStatus.SUCCESS,
            )
            .order_by(DocumentReadSnapshot.id.desc())
        )
        return self.session.scalar(statement)

    def get_latest_source_read(
        self, attachment_id: int, file_sha256: str
    ) -> DocumentReadSnapshot | None:
        """返回当前附件版本最新的非 OCR 读取结果，作为是否需要 OCR 的依据。"""

        statement = (
            select(DocumentReadSnapshot)
            .where(
                DocumentReadSnapshot.attachment_id == attachment_id,
                DocumentReadSnapshot.file_sha256 == file_sha256,
                DocumentReadSnapshot.read_method != "ocr",
            )
            .order_by(DocumentReadSnapshot.id.desc())
        )
        return self.session.scalar(statement)

    def list_for_task(self, task_id: int) -> list[DocumentReadSnapshot]:
        statement = (
            select(DocumentReadSnapshot)
            .join(ApprovalAttachment)
            .where(ApprovalAttachment.task_id == task_id)
            .order_by(DocumentReadSnapshot.id)
        )
        return list(self.session.scalars(statement))

    def add(self, snapshot: DocumentReadSnapshot) -> DocumentReadSnapshot:
        self.session.add(snapshot)
        self.session.flush()
        return snapshot
