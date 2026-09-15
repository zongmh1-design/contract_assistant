from sqlalchemy.orm import Session

from app.models import DocumentReadSnapshot
from app.repositories import AttachmentRepository, DocumentReadRepository


class DocumentReadSnapshotNotFoundError(LookupError):
    pass


class DocumentSourceSelector:
    """集中选择当前主附件、当前 SHA 对应的最新成功读取快照。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def select_for_task(self, task_id: int) -> DocumentReadSnapshot:
        attachment = AttachmentRepository(
            self.session
        ).get_main_downloaded_attachment(task_id)
        if attachment is None or not attachment.sha256:
            raise DocumentReadSnapshotNotFoundError(
                "DOCUMENT_READ_SNAPSHOT_NOT_FOUND: 没有可用的主合同附件版本"
            )
        snapshot = DocumentReadRepository(
            self.session
        ).get_latest_success_for_attachment(attachment.id, attachment.sha256)
        if snapshot is None:
            raise DocumentReadSnapshotNotFoundError(
                "DOCUMENT_READ_SNAPSHOT_NOT_FOUND: 当前附件版本没有成功读取快照"
            )
        return snapshot
