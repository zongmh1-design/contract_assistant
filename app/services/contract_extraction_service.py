from sqlalchemy.orm import Session

from app.core.task_state import InvalidTaskStateError
from app.models import ContractParse, ContractParseStatus, TaskStatus
from app.parsers import (
    ContractExtractor,
    DeterministicContractExtractor,
    failed_contract_extraction,
)
from app.repositories import ContractParseRepository, TaskRepository
from app.schemas import (
    ContractBasicInfo,
    ContractClauses,
    ExtractStatus,
    StructuredContractExtraction,
)
from app.services.document_source_selector import (
    DocumentReadSnapshotNotFoundError,
    DocumentSourceSelector,
)
from app.services.task_state_service import TaskNotFoundError, TaskStateService


class ContractExtractionService:
    def __init__(
        self,
        session: Session,
        extractor: ContractExtractor | None = None,
    ) -> None:
        self.session = session
        self.extractor = extractor or DeterministicContractExtractor()

    def parse_contract(self, task_id: int) -> ContractParse:
        task = TaskRepository(self.session).get_task(task_id)
        if task is None:
            self.session.rollback()
            raise TaskNotFoundError(f"任务不存在: {task_id}")
        if task.task_status != TaskStatus.PARSING:
            current_status = task.task_status.value
            self.session.rollback()
            raise InvalidTaskStateError(
                f"任务处于 {current_status}，不能提取合同字段"
            )

        try:
            document_read = DocumentSourceSelector(self.session).select_for_task(task_id)
        except DocumentReadSnapshotNotFoundError as error:
            self.session.rollback()
            self._block_without_parse(task_id, str(error))
            raise

        repository = ContractParseRepository(self.session)
        reusable = repository.find_reusable(
            document_read.id,
            self.extractor.name,
            self.extractor.version,
        )
        if reusable is not None:
            self.session.expunge(reusable)
            self.session.rollback()
            with self.session.begin():
                TaskRepository(self.session).add_log(
                    task_id,
                    "info",
                    "CONTRACT_PARSE_REUSED",
                    (
                        f"复用合同解析 {reusable.id}，读取快照: "
                        f"{reusable.document_read_snapshot_id}，Extractor: "
                        f"{self.extractor.name}@{self.extractor.version}"
                    ),
                )
            return reusable

        self.session.expunge(document_read)
        self.session.rollback()
        try:
            extraction = self.extractor.extract(document_read)
        except Exception as error:
            return self._save_failed_parse_and_block(task_id, document_read.id, error)

        parse_status = self.status_for(extraction)
        with self.session.begin():
            contract_parse = repository.add(
                ContractParse(
                    document_read_snapshot_id=document_read.id,
                    basic_info_json=extraction.basic_info.model_dump(mode="json"),
                    clause_info_json=extraction.clauses.model_dump(mode="json"),
                    parse_status=parse_status,
                    parse_error=None,
                    extractor_name=self.extractor.name,
                    extractor_version=self.extractor.version,
                )
            )
            TaskRepository(self.session).add_log(
                task_id,
                "info",
                "CONTRACT_PARSE_CREATED",
                (
                    f"合同结构化提取完成，解析: {contract_parse.id}，"
                    f"状态: {parse_status.value}，读取快照: {document_read.id}"
                ),
            )
        return contract_parse

    @staticmethod
    def status_for(
        extraction: StructuredContractExtraction,
    ) -> ContractParseStatus:
        facts = [
            getattr(extraction.basic_info, field_name)
            for field_name in ContractBasicInfo.model_fields
        ] + [
            getattr(extraction.clauses, field_name)
            for field_name in ContractClauses.model_fields
        ]
        if all(fact.extract_status == ExtractStatus.FOUND for fact in facts):
            return ContractParseStatus.SUCCESS
        return ContractParseStatus.PARTIAL

    def _save_failed_parse_and_block(
        self, task_id: int, document_read_snapshot_id: int, error: Exception
    ) -> ContractParse:
        error_message = f"CONTRACT_EXTRACTION_FAILED: {error}"
        failed = failed_contract_extraction()
        with self.session.begin():
            contract_parse = ContractParseRepository(self.session).add(
                ContractParse(
                    document_read_snapshot_id=document_read_snapshot_id,
                    basic_info_json=failed.basic_info.model_dump(mode="json"),
                    clause_info_json=failed.clauses.model_dump(mode="json"),
                    parse_status=ContractParseStatus.FAILED,
                    parse_error=error_message,
                    extractor_name=self.extractor.name,
                    extractor_version=self.extractor.version,
                )
            )
            TaskRepository(self.session).add_log(
                task_id,
                "error",
                "CONTRACT_EXTRACTION_FAILED",
                f"{error_message}，合同解析: {contract_parse.id}",
            )
        TaskStateService(self.session).block_task(
            task_id, error_message, stage="field_extraction"
        )
        return contract_parse

    def _block_without_parse(self, task_id: int, error_message: str) -> None:
        with self.session.begin():
            TaskRepository(self.session).add_log(
                task_id,
                "error",
                "DOCUMENT_READ_SNAPSHOT_NOT_FOUND",
                error_message,
            )
        TaskStateService(self.session).block_task(
            task_id, error_message, stage="field_extraction"
        )
