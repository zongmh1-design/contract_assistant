from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApprovalTask, TaskLog


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_task(self, task_id: int) -> ApprovalTask | None:
        return self.session.get(ApprovalTask, task_id)

    def get_by_instance_id(self, instance_id: str) -> ApprovalTask | None:
        statement = select(ApprovalTask).where(ApprovalTask.instance_id == instance_id)
        return self.session.scalar(statement)

    def get_by_approval_code(self, approval_code: str) -> ApprovalTask | None:
        statement = select(ApprovalTask).where(ApprovalTask.approval_code == approval_code)
        return self.session.scalar(statement)

    def list_tasks(self) -> list[ApprovalTask]:
        statement = select(ApprovalTask).order_by(ApprovalTask.id)
        return list(self.session.scalars(statement))

    def add_task(self, task: ApprovalTask) -> ApprovalTask:
        self.session.add(task)
        self.session.flush()
        return task

    def add_log(
        self,
        task_id: int,
        log_level: str,
        log_type: str,
        log_content: str,
    ) -> TaskLog:
        task_log = TaskLog(
            task_id=task_id,
            log_level=log_level,
            log_type=log_type,
            log_content=log_content,
        )
        self.session.add(task_log)
        self.session.flush()
        return task_log

    def list_logs(self, task_id: int) -> list[TaskLog]:
        statement = (
            select(TaskLog).where(TaskLog.task_id == task_id).order_by(TaskLog.id)
        )
        return list(self.session.scalars(statement))

    def has_rule_review_checkpoint(
        self, task_id: int, contract_parse_id: int, rule_set_fingerprint: str
    ) -> bool:
        marker = (
            f"ContractParse: {contract_parse_id}，"
            f"RuleSet: {rule_set_fingerprint}，"
        )
        statement = select(TaskLog.id).where(
            TaskLog.task_id == task_id,
            TaskLog.log_type.in_(["RULE_REVIEW_COMPLETED", "RULE_REVIEW_REUSED"]),
            TaskLog.log_content.contains(marker),
        )
        return self.session.scalar(statement) is not None
