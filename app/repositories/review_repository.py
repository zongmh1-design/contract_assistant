from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import (
    ApprovalAttachment,
    ContractParse,
    DocumentReadSnapshot,
    LlmRuleEvaluation,
    MatchMode,
    ReviewRule,
    ReviewResult,
    ReviewStatus,
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

    def list_current_for_parse(self, contract_parse_id: int) -> list[RuleHit]:
        deterministic_statement = (
            select(RuleHit)
            .options(joinedload(RuleHit.rule))
            .join(ReviewRule)
            .where(
                RuleHit.contract_parse_id == contract_parse_id,
                ReviewRule.rule_status == RuleStatus.ACTIVE,
                ReviewRule.match_mode != MatchMode.LLM_SEMANTIC,
                RuleHit.rule_version == ReviewRule.rule_version,
            )
            .order_by(RuleHit.id)
        )
        hits = list(self.session.scalars(deterministic_statement))
        evaluations = list(
            self.session.scalars(
                select(LlmRuleEvaluation)
                .options(joinedload(LlmRuleEvaluation.rule_hit).joinedload(RuleHit.rule))
                .join(ReviewRule)
                .where(
                    LlmRuleEvaluation.contract_parse_id == contract_parse_id,
                    ReviewRule.rule_status == RuleStatus.ACTIVE,
                    ReviewRule.match_mode == MatchMode.LLM_SEMANTIC,
                    LlmRuleEvaluation.rule_version == ReviewRule.rule_version,
                )
                .order_by(LlmRuleEvaluation.id.desc())
            )
        )
        latest_rule_ids: set[int] = set()
        for evaluation in evaluations:
            if evaluation.rule_id in latest_rule_ids:
                continue
            latest_rule_ids.add(evaluation.rule_id)
            if evaluation.rule_hit is not None:
                hits.append(evaluation.rule_hit)
        return sorted(hits, key=lambda hit: hit.id)


class LlmRuleEvaluationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_reusable(
        self,
        contract_parse_id: int,
        rule_id: int,
        rule_version: str,
        evaluation_fingerprint: str,
    ) -> LlmRuleEvaluation | None:
        statement = (
            select(LlmRuleEvaluation)
            .options(joinedload(LlmRuleEvaluation.rule_hit).joinedload(RuleHit.rule))
            .where(
                LlmRuleEvaluation.contract_parse_id == contract_parse_id,
                LlmRuleEvaluation.rule_id == rule_id,
                LlmRuleEvaluation.rule_version == rule_version,
                LlmRuleEvaluation.evaluation_fingerprint == evaluation_fingerprint,
                LlmRuleEvaluation.evaluation_status != "degraded",
            )
            .order_by(LlmRuleEvaluation.id.desc())
        )
        return self.session.scalar(statement)

    def add(self, evaluation: LlmRuleEvaluation) -> LlmRuleEvaluation:
        self.session.add(evaluation)
        self.session.flush()
        return evaluation

    def latest_for_active_semantic_rules(
        self, contract_parse_id: int
    ) -> dict[int, LlmRuleEvaluation]:
        statement = (
            select(LlmRuleEvaluation)
            .join(ReviewRule)
            .where(
                LlmRuleEvaluation.contract_parse_id == contract_parse_id,
                ReviewRule.rule_status == RuleStatus.ACTIVE,
                ReviewRule.match_mode == MatchMode.LLM_SEMANTIC,
                LlmRuleEvaluation.rule_version == ReviewRule.rule_version,
            )
            .order_by(LlmRuleEvaluation.id.desc())
        )
        latest: dict[int, LlmRuleEvaluation] = {}
        for evaluation in self.session.scalars(statement):
            latest.setdefault(evaluation.rule_id, evaluation)
        return latest


class ReviewResultRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_reusable(
        self,
        contract_parse_id: int,
        rule_set_fingerprint: str,
        rule_hit_fingerprint: str,
        review_version: str,
    ) -> ReviewResult | None:
        statement = (
            select(ReviewResult)
            .options(
                joinedload(ReviewResult.rule_hits).joinedload(RuleHit.rule)
            )
            .where(
                ReviewResult.contract_parse_id == contract_parse_id,
                ReviewResult.rule_set_fingerprint == rule_set_fingerprint,
                ReviewResult.rule_hit_fingerprint == rule_hit_fingerprint,
                ReviewResult.review_version == review_version,
                ReviewResult.review_status.in_(
                    [ReviewStatus.COMPLETED, ReviewStatus.PARTIAL]
                ),
            )
            .order_by(ReviewResult.id.desc())
        )
        return self.session.scalars(statement).unique().first()

    def list_for_task(self, task_id: int) -> list[ReviewResult]:
        statement = (
            select(ReviewResult)
            .options(
                joinedload(ReviewResult.rule_hits).joinedload(RuleHit.rule)
            )
            .join(ContractParse)
            .join(DocumentReadSnapshot)
            .join(ApprovalAttachment)
            .where(ApprovalAttachment.task_id == task_id)
            .order_by(ReviewResult.id)
        )
        return list(self.session.scalars(statement).unique())

    def get_latest_current(
        self,
        contract_parse_id: int,
        rule_set_fingerprint: str,
        rule_hit_fingerprint: str,
    ) -> ReviewResult | None:
        statement = (
            select(ReviewResult)
            .options(joinedload(ReviewResult.rule_hits))
            .where(
                ReviewResult.contract_parse_id == contract_parse_id,
                ReviewResult.rule_set_fingerprint == rule_set_fingerprint,
                ReviewResult.rule_hit_fingerprint == rule_hit_fingerprint,
                ReviewResult.review_status.in_([ReviewStatus.COMPLETED, ReviewStatus.PARTIAL]),
            )
            .order_by(ReviewResult.id.desc())
        )
        return self.session.scalars(statement).unique().first()

    def add(self, result: ReviewResult) -> ReviewResult:
        self.session.add(result)
        self.session.flush()
        return result
