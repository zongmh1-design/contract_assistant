from pathlib import Path

from sqlalchemy.orm import Session

from app.core.task_state import InvalidTaskStateError
from app.models import DocumentReadSnapshot, DocumentReadStatus, TaskStatus
from app.parsers import DocumentReaderRouter
from app.repositories import (
    AttachmentRepository,
    DocumentReadRepository,
    TaskRepository,
)
from app.schemas import DocumentReadResult, DocumentTextBlock
from app.services.task_state_service import TaskNotFoundError, TaskStateService


DEFAULT_READER_VERSION = "1.0"


class DocumentReadingService:
    def __init__(
        self,
        session: Session,
        reader_router: DocumentReaderRouter | None = None,
        reader_version: str = DEFAULT_READER_VERSION,
    ) -> None:
        self.session = session
        self.reader_router = reader_router or DocumentReaderRouter()
        self.reader_version = reader_version

    def read_main_document(self, task_id: int) -> DocumentReadResult:
        task = TaskRepository(self.session).get_task(task_id)
        if task is None:
            self.session.rollback()
            raise TaskNotFoundError(f"任务不存在: {task_id}")
        if task.task_status != TaskStatus.PARSING:
            current_status = task.task_status.value
            self.session.rollback()
            raise InvalidTaskStateError(
                f"任务处于 {current_status}，不能读取合同文档"
            )

        attachment = AttachmentRepository(
            self.session
        ).get_main_downloaded_attachment(task_id)
        if attachment is None:
            self.session.rollback()
            result = self._failure_result(
                document_id=None,
                file_type="unknown",
                error_code="DOCUMENT_FILE_NOT_FOUND",
                error_message="任务没有已成功下载的主合同附件",
            )
            return self._record_failure_and_block(task_id, result)

        attachment_id = attachment.id
        file_type = attachment.file_type.lower().lstrip(".")
        file_path = attachment.file_path
        file_sha256 = attachment.sha256
        read_method = self.reader_router.read_method_for(file_type)

        if file_sha256:
            reusable = DocumentReadRepository(self.session).find_reusable_success(
                attachment_id,
                file_sha256,
                self.reader_version,
            )
            if reusable is not None:
                result = self._snapshot_to_result(reusable)
                snapshot_id = reusable.id
                self.session.rollback()
                with self.session.begin():
                    TaskRepository(self.session).add_log(
                        task_id,
                        "info",
                        "DOCUMENT_READ_REUSED",
                        (
                            f"复用文档读取快照 {snapshot_id}，附件 SHA-256: "
                            f"{file_sha256}，Reader 版本: {self.reader_version}"
                        ),
                    )
                return result

        self.session.rollback()

        if not file_sha256:
            result = self._failure_result(
                attachment_id,
                file_type,
                "DOCUMENT_READ_FAILED",
                "附件记录缺少 SHA-256，无法建立可靠的读取版本",
            )
            return self._record_failure_and_block(task_id, result)

        if not file_path:
            result = self._failure_result(
                attachment_id,
                file_type,
                "DOCUMENT_FILE_NOT_FOUND",
                "附件记录没有本地文件路径",
            )
            return self._record_failure_and_block(
                task_id, result, attachment_id, file_sha256, read_method
            )

        path = Path(file_path)
        if not path.is_file():
            result = self._failure_result(
                attachment_id,
                file_type,
                "DOCUMENT_FILE_NOT_FOUND",
                f"合同文件不存在: {path}",
            )
            return self._record_failure_and_block(
                task_id, result, attachment_id, file_sha256, read_method
            )

        try:
            file_size = path.stat().st_size
        except OSError as error:
            result = self._failure_result(
                attachment_id,
                file_type,
                "DOCUMENT_READ_FAILED",
                f"无法访问合同文件: {error}",
            )
            return self._record_failure_and_block(
                task_id, result, attachment_id, file_sha256, read_method
            )

        if file_size == 0:
            result = self._failure_result(
                attachment_id,
                file_type,
                "DOCUMENT_FILE_EMPTY",
                "合同文件大小为 0 字节",
            )
            return self._record_failure_and_block(
                task_id, result, attachment_id, file_sha256, read_method
            )

        result = self.reader_router.read(attachment_id, path, file_type)
        if result.read_status != DocumentReadStatus.SUCCESS:
            return self._record_failure_and_block(
                task_id, result, attachment_id, file_sha256, read_method
            )

        with self.session.begin():
            snapshot = self._add_snapshot(
                attachment_id, file_sha256, read_method, result
            )
            TaskRepository(self.session).add_log(
                task_id,
                "info",
                "DOCUMENT_READ_SUCCESS",
                (
                    f"文档读取成功并保存快照 {snapshot.id}，类型: {result.file_type}，"
                    f"文本块: {len(result.blocks)}，页数: {result.page_count}"
                ),
            )
        return result

    @staticmethod
    def _failure_result(
        document_id: int | None,
        file_type: str,
        error_code: str,
        error_message: str,
    ) -> DocumentReadResult:
        return DocumentReadResult(
            document_id=document_id,
            file_type=file_type,
            text="",
            blocks=[],
            page_count=None,
            requires_ocr=False,
            read_status=DocumentReadStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
        )

    def _add_snapshot(
        self,
        attachment_id: int,
        file_sha256: str,
        read_method: str,
        result: DocumentReadResult,
    ) -> DocumentReadSnapshot:
        snapshot = DocumentReadSnapshot(
            attachment_id=attachment_id,
            read_method=read_method,
            file_sha256=file_sha256,
            file_type=result.file_type,
            text=result.text,
            blocks_json=[block.model_dump(mode="json") for block in result.blocks],
            page_count=result.page_count,
            requires_ocr=result.requires_ocr,
            read_status=result.read_status,
            error_code=result.error_code,
            error_message=result.error_message,
            reader_version=self.reader_version,
        )
        return DocumentReadRepository(self.session).add(snapshot)

    @staticmethod
    def _snapshot_to_result(snapshot: DocumentReadSnapshot) -> DocumentReadResult:
        return DocumentReadResult(
            document_id=snapshot.attachment_id,
            file_type=snapshot.file_type,
            text=snapshot.text,
            blocks=[
                DocumentTextBlock.model_validate(block)
                for block in snapshot.blocks_json
            ],
            page_count=snapshot.page_count,
            requires_ocr=snapshot.requires_ocr,
            read_status=snapshot.read_status,
            error_code=snapshot.error_code,
            error_message=snapshot.error_message,
        )

    def _record_failure_and_block(
        self,
        task_id: int,
        result: DocumentReadResult,
        attachment_id: int | None = None,
        file_sha256: str | None = None,
        read_method: str = "unknown",
    ) -> DocumentReadResult:
        error_code = result.error_code or "DOCUMENT_READ_FAILED"
        error_message = result.error_message or "文档读取失败"
        log_level = (
            "warning"
            if result.read_status == DocumentReadStatus.OCR_REQUIRED
            else "error"
        )
        with self.session.begin():
            snapshot_id: int | None = None
            if attachment_id is not None and file_sha256 is not None:
                snapshot = self._add_snapshot(
                    attachment_id, file_sha256, read_method, result
                )
                snapshot_id = snapshot.id
            snapshot_note = f"，读取快照: {snapshot_id}" if snapshot_id else ""
            TaskRepository(self.session).add_log(
                task_id,
                log_level,
                error_code,
                f"{error_code}: {error_message}{snapshot_note}",
            )

        TaskStateService(self.session).block_task(
            task_id,
            f"{error_code}: {error_message}",
            stage="document_reading",
        )
        return result
