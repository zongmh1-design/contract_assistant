from sqlalchemy.orm import Session

from app.integrations.approval.gateway import ApprovalGateway
from app.repositories import TaskRepository
from app.services.task_state_service import TaskNotFoundError, TaskStateService


class MockFailureNotConfiguredError(ValueError):
    pass


class MockFailureService:
    def __init__(self, session: Session, gateway: ApprovalGateway) -> None:
        self.session = session
        self.gateway = gateway

    def simulate_parsing_failure(self, task_id: int):
        task = TaskRepository(self.session).get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"任务不存在: {task_id}")

        instance_id = task.instance_id
        # 上面的只读查询会开启隐式事务；结束它后再由状态服务管理完整写事务。
        self.session.rollback()
        detail = self.gateway.get_contract_approval(instance_id)
        if not detail["simulate_parsing_failure"]:
            raise MockFailureNotConfiguredError("该 Mock 审批未配置解析失败")

        reason = detail["failure_reason"] or "Mock 解析失败"
        return TaskStateService(self.session).block_task(task_id, reason, stage="parsing")
