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
