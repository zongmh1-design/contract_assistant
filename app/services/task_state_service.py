from sqlalchemy.orm import Session

from app.core.task_state import InvalidTaskStateError, change_task_status
from app.models import ApprovalTask, TaskStatus
from app.repositories import TaskRepository


class TaskNotFoundError(LookupError):
    pass


class TaskStateService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = TaskRepository(session)

    def transition_task(self, task_id: int, target_status: TaskStatus) -> ApprovalTask:
        with self.session.begin():
            task = self._require_task(task_id)
            self._transition_and_log(task, target_status)
        return task

    def start_parsing(self, task_id: int) -> ApprovalTask:
        with self.session.begin():
            task = self._require_task(task_id)
            self._transition_and_log(task, TaskStatus.PARSING)
            task.blocked_stage = None
            task.blocked_reason = None
            task.retry_target = None
        return task

    def start_reviewing(self, task_id: int) -> ApprovalTask:
        with self.session.begin():
            task = self._require_task(task_id)
            self._transition_and_log(task, TaskStatus.REVIEWING)
            task.blocked_stage = None
            task.blocked_reason = None
            task.retry_target = None
        return task

    def block_task(self, task_id: int, reason: str, stage: str = "parsing") -> ApprovalTask:
        clean_reason = reason.strip()
        if not clean_reason:
            raise ValueError("blocked 原因不能为空")

        with self.session.begin():
            task = self._require_task(task_id)
            self._transition_and_log(task, TaskStatus.BLOCKED)
            task.blocked_stage = stage
            task.blocked_reason = clean_reason
            task.retry_target = TaskStatus.PARSING.value
            self.repository.add_log(
                task.id,
                "error",
                "task_blocked",
                f"任务在 {stage} 阶段进入 blocked，原因：{clean_reason}",
            )
        return task

    def retry_task(self, task_id: int) -> ApprovalTask:
        with self.session.begin():
            task = self._require_task(task_id)
            if task.retry_target != TaskStatus.PARSING.value:
                raise ValueError("当前任务没有可执行的 parsing 重试检查点")

            previous_reason = task.blocked_reason
            self._transition_and_log(task, TaskStatus.PARSING)
            task.retry_count += 1
            task.blocked_stage = None
            task.blocked_reason = None
            task.retry_target = None
            self.repository.add_log(
                task.id,
                "info",
                "manual_retry",
                f"人工重试任务，第 {task.retry_count} 次；上次阻塞原因：{previous_reason}",
            )
        return task

    def resume_for_ocr(self, task_id: int) -> ApprovalTask:
        """只恢复文档读取阻塞，不重新执行附件下载。"""

        with self.session.begin():
            task = self._require_task(task_id)
            if task.task_status == TaskStatus.PARSING:
                return task
            if (
                task.task_status != TaskStatus.BLOCKED
                or task.blocked_stage != "document_reading"
            ):
                raise InvalidTaskStateError("当前任务不能从 OCR 专用入口恢复")

            previous_reason = task.blocked_reason
            self._transition_and_log(task, TaskStatus.PARSING)
            task.retry_count += 1
            task.blocked_stage = None
            task.blocked_reason = None
            task.retry_target = None
            self.repository.add_log(
                task.id,
                "info",
                "DOCUMENT_OCR_RESUMED",
                f"从文档读取阻塞点恢复 OCR；上次原因：{previous_reason}",
            )
        return task

    def _require_task(self, task_id: int) -> ApprovalTask:
        task = self.repository.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"任务不存在: {task_id}")
        return task

    def _transition_and_log(
        self, task: ApprovalTask, target_status: TaskStatus
    ) -> None:
        previous_status = change_task_status(task, target_status)
        self.repository.add_log(
            task.id,
            "info",
            "status_change",
            f"任务状态从 {previous_status.value} 变更为 {target_status.value}",
        )
