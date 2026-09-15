from pathlib import Path

from sqlalchemy.orm import Session

from app.integrations.approval import ApprovalGateway
from app.models import ApprovalTask, TaskStatus
from app.repositories import TaskRepository
from app.services.attachment_preparation_service import AttachmentPreparationService
from app.services.comment_writeback_service import CommentWritebackService
from app.services.task_state_service import TaskNotFoundError


class RetryStageNotSupportedError(RuntimeError):
    pass


class TaskRetryService:
    """按 blocked_stage 选择已明确支持的最小恢复路径。"""

    def __init__(
        self,
        session: Session,
        gateway: ApprovalGateway,
        storage_root: Path,
    ) -> None:
        self.session = session
        self.gateway = gateway
        self.storage_root = storage_root

    def retry(self, task_id: int) -> ApprovalTask:
        task = TaskRepository(self.session).get_task(task_id)
        if task is None:
            self.session.rollback()
            raise TaskNotFoundError(f"任务不存在: {task_id}")
        if task.task_status != TaskStatus.BLOCKED:
            current_status = task.task_status.value
            self.session.rollback()
            raise RetryStageNotSupportedError(
                f"任务处于 {current_status}，不是可重试的 blocked 任务"
            )
        blocked_stage = task.blocked_stage
        self.session.rollback()

        if blocked_stage == "comment_writeback":
            return CommentWritebackService(
                self.session, self.gateway
            ).retry_comment_write(task_id).task
        if blocked_stage in {"attachment_preparation", "parsing"}:
            return AttachmentPreparationService(
                self.session, self.gateway, self.storage_root
            ).retry_and_prepare(task_id).task
        if blocked_stage == "document_reading":
            raise RetryStageNotSupportedError(
                "document_reading 需使用 /read-document 或 /ocr 专用入口恢复"
            )
        if blocked_stage == "field_extraction":
            raise RetryStageNotSupportedError(
                "field_extraction 尚无通用恢复入口，请从 parsing 检查点重新提取"
            )
        if blocked_stage == "reviewing":
            raise RetryStageNotSupportedError(
                "reviewing 可能来自规则或结果生成，尚需按具体错误选择恢复入口"
            )
        raise RetryStageNotSupportedError(f"不支持的 blocked_stage: {blocked_stage}")
