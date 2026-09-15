from sqlalchemy.orm import Session

from app.models import ContractParse
from app.repositories import ContractParseRepository
from app.services.document_source_selector import (
    DocumentReadSnapshotNotFoundError,
    DocumentSourceSelector,
)


class ContractParseNotFoundError(LookupError):
    pass


class ContractParseSelector:
    """从当前主附件的有效读取快照选择最新可用 ContractParse。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def select_for_task(self, task_id: int) -> ContractParse:
        try:
            snapshot = DocumentSourceSelector(self.session).select_for_task(task_id)
        except DocumentReadSnapshotNotFoundError as error:
            raise ContractParseNotFoundError(
                "CONTRACT_PARSE_NOT_FOUND: 当前合同没有有效读取来源"
            ) from error
        contract_parse = ContractParseRepository(
            self.session
        ).get_latest_usable_for_snapshot(snapshot.id)
        if contract_parse is None:
            raise ContractParseNotFoundError(
                "CONTRACT_PARSE_NOT_FOUND: 当前读取快照没有可用合同解析结果"
            )
        return contract_parse
