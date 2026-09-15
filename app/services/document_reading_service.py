from pathlib import Path

from sqlalchemy.orm import Session

from app.core.task_state import InvalidTaskStateError
from app.models import TaskStatus
from app.parsers import DocumentReaderRouter
from app.repositories import AttachmentRepository, TaskRepository
from app.schemas import DocumentReadResult, DocumentReadStatus
from app.services.task_state_service import TaskNotFoundError, TaskStateService


class DocumentReadingService:
    def __init__(
        self,
        session: Session,
        reader_router: DocumentReaderRouter | None = None,
    ) -> None:
        self.session = session
        self.reader_router = reader_router or DocumentReaderRouter()

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

        document_id = attachment.id
        file_type = attachment.file_type
        file_path = attachment.file_path
        self.session.rollback()

        if not file_path:
            result = self._failure_result(
                document_id,
                file_type,
                "DOCUMENT_FILE_NOT_FOUND",
                "附件记录没有本地文件路径",
            )
            return self._record_failure_and_block(task_id, result)

        path = Path(file_path)
        if not path.is_file():
            result = self._failure_result(
                document_id,
                file_type,
                "DOCUMENT_FILE_NOT_FOUND",
                f"合同文件不存在: {path}",
            )
            return self._record_failure_and_block(task_id, result)

        try:
            file_size = path.stat().st_size
        except OSError as error:
            result = self._failure_result(
                document_id,
                file_type,
                "DOCUMENT_READ_FAILED",
                f"无法访问合同文件: {error}",
            )
            return self._record_failure_and_block(task_id, result)

        if file_size == 0:
            result = self._failure_result(
                document_id,
                file_type,
                "DOCUMENT_FILE_EMPTY",
                "合同文件大小为 0 字节",
            )
            return self._record_failure_and_block(task_id, result)

        result = self.reader_router.read(document_id, path, file_type)
        if result.read_status != DocumentReadStatus.SUCCESS:
            return self._record_failure_and_block(task_id, result)

        with self.session.begin():
            TaskRepository(self.session).add_log(
                task_id,
                "info",
                "DOCUMENT_READ_SUCCESS",
                (
                    f"文档读取成功，类型: {result.file_type}，"
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

    def _record_failure_and_block(
        self, task_id: int, result: DocumentReadResult
    ) -> DocumentReadResult:
        error_code = result.error_code or "DOCUMENT_READ_FAILED"
        error_message = result.error_message or "文档读取失败"
        log_level = (
            "warning"
            if result.read_status == DocumentReadStatus.OCR_REQUIRED
            else "error"
        )
        with self.session.begin():
            TaskRepository(self.session).add_log(
                task_id,
                log_level,
                error_code,
                f"{error_code}: {error_message}",
            )

        TaskStateService(self.session).block_task(
            task_id,
            f"{error_code}: {error_message}",
            stage="document_reading",
        )
        return result
