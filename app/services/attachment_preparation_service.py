from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re

from sqlalchemy.orm import Session

from app.core.task_state import InvalidTaskStateError
from app.integrations.approval.gateway import (
    ApprovalAttachmentData,
    AttachmentDownloadError,
    ApprovalGateway,
)
from app.models import ApprovalAttachment, ApprovalTask, DownloadStatus, TaskStatus
from app.repositories import AttachmentRepository, TaskRepository
from app.services.task_state_service import TaskNotFoundError, TaskStateService


SUPPORTED_FILE_TYPES = {"pdf", "docx", "jpg", "jpeg", "png"}
MAIN_CONTRACT_KEYWORDS = ("合同", "协议", "contract", "agreement")
ILLEGAL_PATH_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class AttachmentPreparationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AttachmentPreparationResult:
    task: ApprovalTask
    attachment: ApprovalAttachment | None
    event: str


def select_main_contract(
    attachments: list[ApprovalAttachmentData],
) -> ApprovalAttachmentData | None:
    """按明确标记、文件名关键词、原始顺序依次识别主合同。"""
    for attachment in attachments:
        if attachment["is_main_contract"]:
            return attachment

    for attachment in attachments:
        file_name = attachment["file_name"].casefold()
        if any(keyword in file_name for keyword in MAIN_CONTRACT_KEYWORDS):
            return attachment
    return None


def safe_path_component(value: str, fallback: str) -> str:
    """保留可识别文本，同时去掉目录和 Windows 非法路径字符。"""
    base_name = value.replace("\\", "/").split("/")[-1]
    cleaned = ILLEGAL_PATH_CHARACTERS.sub("_", base_name).strip().rstrip(". ")
    if cleaned in {"", ".", ".."}:
        return fallback
    return cleaned


def build_attachment_path(
    storage_root: Path,
    approval_code: str,
    attachment_id: str,
    original_file_name: str,
) -> Path:
    safe_approval_code = safe_path_component(approval_code, "unknown_approval")
    safe_attachment_id = safe_path_component(attachment_id, "attachment")
    safe_file_name = safe_path_component(original_file_name, "contract_file")
    approval_directory = (storage_root / safe_approval_code).resolve()
    target_path = (approval_directory / f"{safe_attachment_id}_{safe_file_name}").resolve()
    if target_path.parent != approval_directory:
        raise AttachmentPreparationError("附件保存路径超出审批目录")
    return target_path


def calculate_sha256(content: bytes) -> str:
    return sha256(content).hexdigest()


class AttachmentPreparationService:
    def __init__(
        self,
        session: Session,
        gateway: ApprovalGateway,
        storage_root: Path,
    ) -> None:
        self.session = session
        self.gateway = gateway
        self.storage_root = storage_root.resolve()

    def prepare_main_attachment(self, task_id: int) -> AttachmentPreparationResult:
        task = self._ensure_parsing(task_id)
        detail = self.gateway.get_contract_approval(task.instance_id)
        attachments = detail["attachments"]

        if not attachments:
            return self._block(
                task_id,
                "ATTACHMENT_NOT_FOUND",
                "审批详情中没有附件",
            )

        main_attachment = select_main_contract(attachments)
        if main_attachment is None:
            return self._block(
                task_id,
                "MAIN_CONTRACT_NOT_FOUND",
                "附件中没有明确标记或文件名可识别的主合同",
            )

        file_type = main_attachment["file_type"].lower().lstrip(".")
        if file_type not in SUPPORTED_FILE_TYPES:
            self._record_failed_attachment(
                task_id,
                main_attachment,
                file_type,
                "UNSUPPORTED_ATTACHMENT_TYPE",
            )
            return self._block(
                task_id,
                "UNSUPPORTED_ATTACHMENT_TYPE",
                f"不支持主合同附件类型: {file_type}",
            )

        try:
            content = self.gateway.download_contract_attachment(
                task.instance_id,
                main_attachment["attachment_id"],
                main_attachment["file_name"],
            )
        except AttachmentDownloadError as error:
            self._record_failed_attachment(
                task_id,
                main_attachment,
                file_type,
                f"ATTACHMENT_DOWNLOAD_FAILED: {error}",
            )
            return self._block(
                task_id,
                "ATTACHMENT_DOWNLOAD_FAILED",
                str(error),
            )

        content_sha256 = calculate_sha256(content)
        return self._save_download(
            task,
            main_attachment,
            file_type,
            content,
            content_sha256,
        )

    def retry_and_prepare(self, task_id: int) -> AttachmentPreparationResult:
        TaskStateService(self.session).retry_task(task_id)
        return self.prepare_main_attachment(task_id)

    def _ensure_parsing(self, task_id: int) -> ApprovalTask:
        task = TaskRepository(self.session).get_task(task_id)
        if task is None:
            self.session.rollback()
            raise TaskNotFoundError(f"任务不存在: {task_id}")

        current_status = task.task_status
        self.session.rollback()
        if current_status == TaskStatus.PENDING:
            return TaskStateService(self.session).start_parsing(task_id)
        if current_status != TaskStatus.PARSING:
            raise InvalidTaskStateError(
                f"任务处于 {current_status.value}，不能准备附件"
            )

        task = TaskRepository(self.session).get_task(task_id)
        assert task is not None
        self.session.rollback()
        return task

    def _save_download(
        self,
        task: ApprovalTask,
        metadata: ApprovalAttachmentData,
        file_type: str,
        content: bytes,
        content_sha256: str,
    ) -> AttachmentPreparationResult:
        repository = AttachmentRepository(self.session)
        existing = repository.get_by_external_id(task.id, metadata["attachment_id"])
        existing_path = Path(existing.file_path) if existing and existing.file_path else None

        if (
            existing is not None
            and existing.sha256 == content_sha256
            and existing_path is not None
            and existing_path.is_file()
        ):
            with self.session.begin_nested():
                existing.original_file_name = metadata["file_name"]
                existing.file_type = file_type
                existing.file_size = len(content)
                existing.download_status = DownloadStatus.SUCCESS
                existing.download_error = None
                TaskRepository(self.session).add_log(
                    task.id,
                    "info",
                    "ATTACHMENT_REUSED",
                    f"SHA-256 一致，复用附件: {existing.file_path}",
                )
            self.session.commit()
            return AttachmentPreparationResult(task, existing, "ATTACHMENT_REUSED")

        old_sha256 = existing.sha256 if existing is not None else None
        target_path = build_attachment_path(
            self.storage_root,
            task.approval_code,
            metadata["attachment_id"],
            metadata["file_name"],
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = target_path.with_name(f"{target_path.name}.tmp")
        temporary_path.write_bytes(content)
        temporary_path.replace(target_path)

        event = (
            "ATTACHMENT_UPDATED"
            if existing is not None and old_sha256 is not None
            else "ATTACHMENT_DOWNLOADED"
        )
        with self.session.begin_nested():
            if existing is None:
                existing = ApprovalAttachment(
                    task_id=task.id,
                    external_attachment_id=metadata["attachment_id"],
                    original_file_name=metadata["file_name"],
                    file_type=file_type,
                )
                repository.add(existing)

            existing.original_file_name = metadata["file_name"]
            existing.file_type = file_type
            existing.file_path = str(target_path)
            existing.file_size = len(content)
            existing.sha256 = content_sha256
            existing.is_main_contract = True
            existing.download_status = DownloadStatus.SUCCESS
            existing.download_error = None

            if event == "ATTACHMENT_UPDATED":
                log_content = (
                    f"上游附件内容发生变化，旧 SHA-256: {old_sha256}，"
                    f"新 SHA-256: {content_sha256}"
                )
            else:
                log_content = f"主合同附件下载完成: {target_path}"
            TaskRepository(self.session).add_log(task.id, "info", event, log_content)
        self.session.commit()
        return AttachmentPreparationResult(task, existing, event)

    def _record_failed_attachment(
        self,
        task_id: int,
        metadata: ApprovalAttachmentData,
        file_type: str,
        error_message: str,
    ) -> None:
        repository = AttachmentRepository(self.session)
        existing = repository.get_by_external_id(task_id, metadata["attachment_id"])
        with self.session.begin_nested():
            if existing is None:
                existing = ApprovalAttachment(
                    task_id=task_id,
                    external_attachment_id=metadata["attachment_id"],
                    original_file_name=metadata["file_name"],
                    file_type=file_type,
                )
                repository.add(existing)
            existing.original_file_name = metadata["file_name"]
            existing.file_type = file_type
            existing.is_main_contract = True
            existing.download_status = DownloadStatus.FAILED
            existing.download_error = error_message
            TaskRepository(self.session).add_log(
                task_id,
                "error",
                error_message.split(":", 1)[0],
                error_message,
            )
        self.session.commit()

    def _block(
        self, task_id: int, error_code: str, message: str
    ) -> AttachmentPreparationResult:
        reason = f"{error_code}: {message}"
        task = TaskStateService(self.session).block_task(
            task_id,
            reason,
            stage="attachment_preparation",
        )
        return AttachmentPreparationResult(task, None, error_code)
