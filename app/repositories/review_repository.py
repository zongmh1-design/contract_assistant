from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import (
    ApprovalAttachment,
    ContractParse,
    DocumentReadSnapshot,
    ReviewRule,
    RuleHit,
    RuleStatus,
)


class ReviewRuleRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_rules(self) -> list[ReviewRule]:
        return list(self.session.scalars(select(ReviewRule).order_by(ReviewRule.id)))

    def list_active_rules(self) -> list[ReviewRule]:
        statement = (
            select(ReviewRule)
            .where(ReviewRule.rule_status == RuleStatus.ACTIVE)
            .order_by(ReviewRule.id)
        )
        return list(self.session.scalars(statement))


class RuleHitRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_existing(
        self, contract_parse_id: int, rule_id: int, rule_version: str
    ) -> RuleHit | None:
        statement = select(RuleHit).where(
            RuleHit.contract_parse_id == contract_parse_id,
            RuleHit.rule_id == rule_id,
            RuleHit.rule_version == rule_version,
        )
        return self.session.scalar(statement)

    def add(self, hit: RuleHit) -> RuleHit:
        self.session.add(hit)
        self.session.flush()
        return hit

    def list_for_task(self, task_id: int) -> list[RuleHit]:
        statement = (
            select(RuleHit)
            .options(joinedload(RuleHit.rule))
            .join(ContractParse)
            .join(DocumentReadSnapshot)
            .join(ApprovalAttachment)
            .where(ApprovalAttachment.task_id == task_id)
            .order_by(RuleHit.id)
        )
        return list(self.session.scalars(statement))
