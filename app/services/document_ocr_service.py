from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy.orm import Session

from app.integrations.ocr import (
    OcrEngine,
    OcrEngineError,
    PdfPageRenderer,
    PdfRenderError,
)
from app.models import DocumentReadSnapshot, DocumentReadStatus, TaskStatus
from app.parsers import has_meaningful_text
from app.repositories import (
    AttachmentRepository,
    DocumentReadRepository,
    TaskRepository,
)
from app.schemas import DocumentReadResult, DocumentTextBlock
from app.services.task_state_service import TaskNotFoundError, TaskStateService


SUPPORTED_OCR_FILE_TYPES = {"pdf", "jpg", "jpeg", "png"}


class DocumentOcrNotAllowedError(ValueError):
    pass


class DocumentOcrService:
    def __init__(
        self,
        session: Session,
        ocr_engine: OcrEngine,
        pdf_page_renderer: PdfPageRenderer,
    ) -> None:
        self.session = session
        self.ocr_engine = ocr_engine
        self.pdf_page_renderer = pdf_page_renderer

    def recognize_main_document(self, task_id: int) -> DocumentReadResult:
        task = TaskRepository(self.session).get_task(task_id)
        if task is None:
            self.session.rollback()
            raise TaskNotFoundError(f"任务不存在: {task_id}")
        if task.task_status not in {TaskStatus.PARSING, TaskStatus.BLOCKED}:
            current_status = task.task_status.value
            self.session.rollback()
            raise DocumentOcrNotAllowedError(
                f"任务处于 {current_status}，不能执行 OCR"
            )
        if (
            task.task_status == TaskStatus.BLOCKED
            and task.blocked_stage != "document_reading"
        ):
            blocked_stage = task.blocked_stage
            self.session.rollback()
            raise DocumentOcrNotAllowedError(
                f"任务阻塞于 {blocked_stage}，不能从 OCR 入口恢复"
            )

        attachment = AttachmentRepository(
            self.session
        ).get_main_downloaded_attachment(task_id)
        if attachment is None:
            self.session.rollback()
            raise DocumentOcrNotAllowedError("OCR_INPUT_FILE_NOT_FOUND: 没有主合同附件")

        attachment_id = attachment.id
        file_path = attachment.file_path
        file_sha256 = attachment.sha256
        file_type = attachment.file_type.lower().lstrip(".")
        if not file_sha256:
            self.session.rollback()
            raise DocumentOcrNotAllowedError(
                "OCR_INPUT_FILE_NOT_FOUND: 附件缺少 SHA-256"
            )

        repository = DocumentReadRepository(self.session)
        reusable = repository.find_reusable_ocr_success(
            attachment_id, file_sha256, self.ocr_engine.version
        )
        if reusable is not None:
            result = self._snapshot_to_result(reusable)
            snapshot_id = reusable.id
            self.session.rollback()
            TaskStateService(self.session).resume_for_ocr(task_id)
            with self.session.begin():
                TaskRepository(self.session).add_log(
                    task_id,
                    "info",
                    "DOCUMENT_OCR_REUSED",
                    (
                        f"复用 OCR 快照 {snapshot_id}，附件 SHA-256: {file_sha256}，"
                        f"OCR 版本: {self.ocr_engine.version}"
                    ),
                )
            return result

        source_read = repository.get_latest_source_read(attachment_id, file_sha256)
        if source_read is None:
            self.session.rollback()
            raise DocumentOcrNotAllowedError(
                "OCR_REQUIRED_SNAPSHOT_NOT_FOUND: 当前附件版本没有待读取快照"
            )
        if source_read.read_status == DocumentReadStatus.SUCCESS:
            self.session.rollback()
            raise DocumentOcrNotAllowedError(
                "OCR_NOT_REQUIRED: 当前附件已有正常文本读取结果"
            )
        if source_read.read_status != DocumentReadStatus.OCR_REQUIRED:
            source_status = source_read.read_status.value
            self.session.rollback()
            raise DocumentOcrNotAllowedError(
                f"OCR_REQUIRED_SNAPSHOT_NOT_FOUND: 最新源读取状态为 {source_status}"
            )
        self.session.rollback()

        TaskStateService(self.session).resume_for_ocr(task_id)
        with self.session.begin():
            TaskRepository(self.session).add_log(
                task_id,
                "info",
                "DOCUMENT_OCR_STARTED",
                (
                    f"开始 OCR，附件: {attachment_id}，类型: {file_type}，"
                    f"OCR 版本: {self.ocr_engine.version}"
                ),
            )

        if file_type not in SUPPORTED_OCR_FILE_TYPES:
            return self._fail_and_block(
                task_id,
                attachment_id,
                file_sha256,
                file_type,
                "OCR_FILE_UNSUPPORTED",
                f"OCR 不支持文件类型: {file_type}",
            )
        if not file_path or not Path(file_path).is_file():
            return self._fail_and_block(
                task_id,
                attachment_id,
                file_sha256,
                file_type,
                "OCR_INPUT_FILE_NOT_FOUND",
                f"OCR 输入文件不存在: {file_path}",
            )

        path = Path(file_path)
        try:
            blocks, page_count = self._recognize_file(path, file_type)
        except PdfRenderError as error:
            return self._fail_and_block(
                task_id,
                attachment_id,
                file_sha256,
                file_type,
                "PDF_RENDER_FAILED",
                str(error),
            )
        except OcrEngineError as error:
            return self._fail_and_block(
                task_id,
                attachment_id,
                file_sha256,
                file_type,
                "OCR_ENGINE_FAILED",
                str(error),
            )

        text = "\n\n".join(block.text for block in blocks)
        if not has_meaningful_text(text):
            return self._fail_and_block(
                task_id,
                attachment_id,
                file_sha256,
                file_type,
                "OCR_CONTENT_EMPTY",
                "OCR 没有返回有效文本",
                page_count=page_count,
            )

        result = DocumentReadResult(
            document_id=attachment_id,
            file_type=file_type,
            text=text,
            blocks=blocks,
            page_count=page_count,
            requires_ocr=False,
            read_status=DocumentReadStatus.SUCCESS,
            error_code=None,
            error_message=None,
        )
        with self.session.begin():
            snapshot = self._add_snapshot(
                attachment_id, file_sha256, file_type, result
            )
            TaskRepository(self.session).add_log(
                task_id,
                "info",
                "DOCUMENT_OCR_SUCCESS",
                (
                    f"OCR 成功并保存快照 {snapshot.id}，页数: {page_count}，"
                    f"文本块: {len(blocks)}"
                ),
            )
        return result

    def _recognize_file(
        self, path: Path, file_type: str
    ) -> tuple[list[DocumentTextBlock], int]:
        if file_type != "pdf":
            recognition = self.ocr_engine.recognize_image(path)
            blocks = [
                DocumentTextBlock(text=line.text, page_number=1, block_index=index)
                for index, line in enumerate(recognition.lines)
                if line.text.strip()
            ]
            return blocks, 1

        with TemporaryDirectory(prefix="contract-ocr-") as temporary_directory:
            pages = self.pdf_page_renderer.render_pages(
                path, Path(temporary_directory)
            )
            if not pages:
                raise PdfRenderError("PDF 页面渲染失败: 文档没有可渲染页面")

            blocks: list[DocumentTextBlock] = []
            for page in pages:
                recognition = self.ocr_engine.recognize_image(page.image_path)
                for line in recognition.lines:
                    if line.text.strip():
                        blocks.append(
                            DocumentTextBlock(
                                text=line.text,
                                page_number=page.page_number,
                                block_index=len(blocks),
                            )
                        )
            return blocks, len(pages)

    def _add_snapshot(
        self,
        attachment_id: int,
        file_sha256: str,
        file_type: str,
        result: DocumentReadResult,
    ) -> DocumentReadSnapshot:
        snapshot = DocumentReadSnapshot(
            attachment_id=attachment_id,
            read_method="ocr",
            file_sha256=file_sha256,
            file_type=file_type,
            text=result.text,
            blocks_json=[block.model_dump(mode="json") for block in result.blocks],
            page_count=result.page_count,
            requires_ocr=result.requires_ocr,
            read_status=result.read_status,
            error_code=result.error_code,
            error_message=result.error_message,
            reader_version=self.ocr_engine.version,
        )
        return DocumentReadRepository(self.session).add(snapshot)

    def _fail_and_block(
        self,
        task_id: int,
        attachment_id: int,
        file_sha256: str,
        file_type: str,
        error_code: str,
        error_message: str,
        *,
        page_count: int | None = None,
    ) -> DocumentReadResult:
        result = DocumentReadResult(
            document_id=attachment_id,
            file_type=file_type,
            text="",
            blocks=[],
            page_count=page_count,
            requires_ocr=True,
            read_status=DocumentReadStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
        )
        with self.session.begin():
            snapshot = self._add_snapshot(
                attachment_id, file_sha256, file_type, result
            )
            TaskRepository(self.session).add_log(
                task_id,
                "error",
                error_code,
                f"{error_code}: {error_message}，OCR 快照: {snapshot.id}",
            )
        TaskStateService(self.session).block_task(
            task_id,
            f"{error_code}: {error_message}",
            stage="document_reading",
        )
        return result

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
