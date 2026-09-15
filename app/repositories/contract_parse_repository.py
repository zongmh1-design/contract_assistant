from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ApprovalAttachment,
    ContractParse,
    ContractParseStatus,
    DocumentReadSnapshot,
)


class ContractParseRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_reusable(
        self,
        document_read_snapshot_id: int,
        extractor_name: str,
        extractor_version: str,
    ) -> ContractParse | None:
        statement = (
            select(ContractParse)
            .where(
                ContractParse.document_read_snapshot_id
                == document_read_snapshot_id,
                ContractParse.extractor_name == extractor_name,
                ContractParse.extractor_version == extractor_version,
                ContractParse.parse_status.in_(
                    [ContractParseStatus.SUCCESS, ContractParseStatus.PARTIAL]
                ),
            )
            .order_by(ContractParse.id.desc())
        )
        return self.session.scalar(statement)

    def list_for_task(self, task_id: int) -> list[ContractParse]:
        statement = (
            select(ContractParse)
            .join(DocumentReadSnapshot)
            .join(ApprovalAttachment)
            .where(ApprovalAttachment.task_id == task_id)
            .order_by(ContractParse.id)
        )
        return list(self.session.scalars(statement))

    def add(self, contract_parse: ContractParse) -> ContractParse:
        self.session.add(contract_parse)
        self.session.flush()
        return contract_parse
