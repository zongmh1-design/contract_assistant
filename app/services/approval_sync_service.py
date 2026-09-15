from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.integrations.approval.gateway import ApprovalGateway, PendingApprovalData
from app.models import ApprovalTask, TaskStatus, WriteStatus
from app.repositories import TaskRepository


class ApprovalIdentityConflictError(ValueError):
    """instance_id 与 approval_code 无法唯一指向同一任务。"""


@dataclass(frozen=True)
class SyncResult:
    created_count: int
    updated_count: int
    tasks: list[ApprovalTask]


class ApprovalSyncService:
    def __init__(self, session: Session, gateway: ApprovalGateway) -> None:
        self.session = session
        self.gateway = gateway
        self.repository = TaskRepository(session)

    def sync_pending_tasks(self, limit: int) -> SyncResult:
        items = self.gateway.list_pending_contract_approvals(limit)
        created_count = 0
        updated_count = 0

        with self.session.begin():
            for item in items:
                task, created = self._create_or_update(item)
                if created:
                    created_count += 1
                else:
                    updated_count += 1

        return SyncResult(
            created_count=created_count,
            updated_count=updated_count,
            tasks=self.repository.list_tasks(),
        )

    def _create_or_update(
        self, item: PendingApprovalData
    ) -> tuple[ApprovalTask, bool]:
        by_instance = self.repository.get_by_instance_id(item["instance_id"])
        by_code = self.repository.get_by_approval_code(item["approval_code"])

        if by_instance is not None and by_code is not None and by_instance.id != by_code.id:
            raise ApprovalIdentityConflictError(
                "instance_id 与 approval_code 分别命中了不同任务，拒绝自动合并"
            )
        if by_instance is None and by_code is not None:
            raise ApprovalIdentityConflictError(
                "approval_code 已存在，但 instance_id 不一致，拒绝创建重复任务"
            )
        if by_instance is not None and by_instance.approval_code != item["approval_code"]:
            raise ApprovalIdentityConflictError(
                "instance_id 已存在，但 approval_code 不一致，拒绝更新业务标识"
            )

        existing = by_instance or by_code
        if existing is not None:
            existing.approval_title = item["approval_title"]
            existing.applicant_name = item["applicant_name"]
            existing.applied_at = item["applied_at"]
            self.repository.add_log(
                existing.id,
                "info",
                "duplicate_pull",
                "重复拉取审批任务：保留原任务 ID，并更新可变审批信息",
            )
            return existing, False

        task = ApprovalTask(
            instance_id=item["instance_id"],
            approval_code=item["approval_code"],
            approval_title=item["approval_title"],
            applicant_name=item["applicant_name"],
            applied_at=item["applied_at"],
            task_status=TaskStatus.PENDING,
            write_status=WriteStatus.NOT_WRITTEN,
        )
        self.repository.add_task(task)
        self.repository.add_log(
            task.id,
            "info",
            "task_created",
            "首次拉取审批任务，创建 ApprovalTask，状态为 pending",
        )
        return task, True
