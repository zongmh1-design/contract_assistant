from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApprovalAttachment


class AttachmentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_external_id(
        self, task_id: int, external_attachment_id: str
    ) -> ApprovalAttachment | None:
        statement = select(ApprovalAttachment).where(
            ApprovalAttachment.task_id == task_id,
            ApprovalAttachment.external_attachment_id == external_attachment_id,
        )
        return self.session.scalar(statement)

    def list_for_task(self, task_id: int) -> list[ApprovalAttachment]:
        statement = (
            select(ApprovalAttachment)
            .where(ApprovalAttachment.task_id == task_id)
            .order_by(ApprovalAttachment.id)
        )
        return list(self.session.scalars(statement))

    def add(self, attachment: ApprovalAttachment) -> ApprovalAttachment:
        self.session.add(attachment)
        self.session.flush()
        return attachment
