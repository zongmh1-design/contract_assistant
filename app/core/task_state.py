from app.models import ApprovalTask, TaskStatus


class InvalidTaskStateError(ValueError):
    """任务当前状态不允许执行请求的转换。"""


ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.PARSING},
    TaskStatus.PARSING: {TaskStatus.REVIEWING, TaskStatus.BLOCKED},
    TaskStatus.REVIEWING: {TaskStatus.BLOCKED},
    TaskStatus.BLOCKED: {TaskStatus.PARSING},
    TaskStatus.DONE: set(),
}


def change_task_status(task: ApprovalTask, target_status: TaskStatus) -> TaskStatus:
    """校验并修改状态；业务服务负责在同一事务内写 TaskLog。"""
    previous_status = task.task_status
    allowed_targets = ALLOWED_TRANSITIONS[previous_status]
    if target_status not in allowed_targets:
        raise InvalidTaskStateError(
            f"不允许任务从 {previous_status.value} 转换到 {target_status.value}"
        )

    task.task_status = target_status
    return previous_status
