from sqlalchemy.orm import Session

from app.core.task_state import InvalidTaskStateError, change_task_status
from app.models import ApprovalTask, TaskStatus, WriteStatus
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

    def start_comment_write(self, task_id: int, review_result_id: int) -> ApprovalTask:
        with self.session.begin():
            task = self._require_task(task_id)
            if task.task_status != TaskStatus.REVIEWING:
                raise InvalidTaskStateError("只有 reviewing 任务可以开始评论回写")
            if task.write_status not in {WriteStatus.NOT_WRITTEN, WriteStatus.FAILED}:
                raise InvalidTaskStateError(
                    f"当前评论回写状态为 {task.write_status.value}，不能开始新尝试"
                )
            previous_status = task.write_status
            task.write_status = WriteStatus.WRITING
            self.repository.add_log(
                task.id,
                "info",
                "COMMENT_WRITE_STARTED",
                (
                    f"评论回写状态从 {previous_status.value} 变更为 writing，"
                    f"ReviewResult: {review_result_id}"
                ),
            )
        return task

    def complete_comment_write(
        self, task_id: int, comment_log_id: int, external_comment_id: str
    ) -> ApprovalTask:
        with self.session.begin():
            task = self._require_task(task_id)
            if (
                task.task_status != TaskStatus.REVIEWING
                or task.write_status != WriteStatus.WRITING
            ):
                raise InvalidTaskStateError("任务当前不处于可完成评论回写的状态")
            task.write_status = WriteStatus.SUCCESS
            self.repository.add_log(
                task.id,
                "info",
                "COMMENT_WRITE_SUCCEEDED",
                (
                    f"评论回写成功，CommentLog: {comment_log_id}，"
                    f"外部评论: {external_comment_id}"
                ),
            )
            self._transition_and_log(task, TaskStatus.DONE)
            self.repository.add_log(
                task.id,
                "info",
                "TASK_COMPLETED",
                "合同审查评论已成功写回，任务处理完成",
            )
        return task

    def fail_comment_write(
        self, task_id: int, comment_log_id: int, error_code: str, reason: str
    ) -> ApprovalTask:
        clean_reason = reason.strip()
        if not clean_reason:
            raise ValueError("评论回写失败原因不能为空")
        with self.session.begin():
            task = self._require_task(task_id)
            if (
                task.task_status != TaskStatus.REVIEWING
                or task.write_status != WriteStatus.WRITING
            ):
                raise InvalidTaskStateError("任务当前不处于评论回写中")
            task.write_status = WriteStatus.FAILED
            self.repository.add_log(
                task.id,
                "error",
                "COMMENT_WRITE_FAILED",
                f"{error_code}: {clean_reason}，CommentLog: {comment_log_id}",
            )
            self._transition_and_log(task, TaskStatus.BLOCKED)
            task.blocked_stage = "comment_writeback"
            task.blocked_reason = f"{error_code}: {clean_reason}"
            task.retry_target = TaskStatus.REVIEWING.value
            self.repository.add_log(
                task.id,
                "error",
                "task_blocked",
                (
                    "任务在 comment_writeback 阶段进入 blocked，原因："
                    f"{error_code}: {clean_reason}"
                ),
            )
        return task

    def block_comment_write_precondition(
        self, task_id: int, error_code: str, reason: str
    ) -> ApprovalTask:
        """在尚不能创建 CommentLog 时记录评论阶段前置条件失败。"""

        clean_reason = reason.strip()
        with self.session.begin():
            task = self._require_task(task_id)
            if task.task_status != TaskStatus.REVIEWING:
                raise InvalidTaskStateError("当前任务不能记录评论回写前置失败")
            task.write_status = WriteStatus.FAILED
            self.repository.add_log(
                task.id,
                "error",
                "COMMENT_WRITE_FAILED",
                f"{error_code}: {clean_reason}",
            )
            self._transition_and_log(task, TaskStatus.BLOCKED)
            task.blocked_stage = "comment_writeback"
            task.blocked_reason = f"{error_code}: {clean_reason}"
            task.retry_target = TaskStatus.REVIEWING.value
            self.repository.add_log(
                task.id,
                "error",
                "task_blocked",
                (
                    "任务在 comment_writeback 阶段进入 blocked，原因："
                    f"{error_code}: {clean_reason}"
                ),
            )
        return task

    def resume_comment_writeback(self, task_id: int) -> ApprovalTask:
        with self.session.begin():
            task = self._require_task(task_id)
            if (
                task.task_status != TaskStatus.BLOCKED
                or task.blocked_stage != "comment_writeback"
                or task.retry_target != TaskStatus.REVIEWING.value
                or task.write_status != WriteStatus.FAILED
            ):
                raise InvalidTaskStateError("当前任务不能从评论回写阻塞点恢复")
            previous_reason = task.blocked_reason
            self._transition_and_log(task, TaskStatus.REVIEWING)
            task.retry_count += 1
            task.blocked_stage = None
            task.blocked_reason = None
            task.retry_target = None
            self.repository.add_log(
                task.id,
                "info",
                "COMMENT_WRITE_RETRY_RESUMED",
                f"仅恢复评论回写，第 {task.retry_count} 次；上次原因：{previous_reason}",
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
