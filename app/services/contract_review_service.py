from sqlalchemy.orm import Session

from app.core.task_state import InvalidTaskStateError
from app.models import (
    MatchMode,
    RiskLevel,
    RuleHit,
    RuleHitStatus,
    TaskStatus,
)
from app.repositories import ReviewRuleRepository, RuleHitRepository, TaskRepository
from app.rules import DeterministicRuleEngine, RuleEngine
from app.schemas import RuleHitRead, RuleReviewResponse, RuleReviewSummary
from app.services.contract_parse_selector import (
    ContractParseNotFoundError,
    ContractParseSelector,
)
from app.services.task_state_service import TaskNotFoundError, TaskStateService


class RuleEngineFailedError(RuntimeError):
    pass


class ContractReviewService:
    def __init__(
        self, session: Session, rule_engine: RuleEngine | None = None
    ) -> None:
        self.session = session
        self.rule_engine = rule_engine or DeterministicRuleEngine()

    def run_rules(self, task_id: int) -> RuleReviewResponse:
        task = TaskRepository(self.session).get_task(task_id)
        if task is None:
            self.session.rollback()
            raise TaskNotFoundError(f"任务不存在: {task_id}")
        current_status = task.task_status
        if current_status not in {TaskStatus.PARSING, TaskStatus.REVIEWING}:
            self.session.rollback()
            raise InvalidTaskStateError(
                f"任务处于 {current_status.value}，不能执行合同规则审查"
            )

        try:
            contract_parse = ContractParseSelector(self.session).select_for_task(task_id)
        except ContractParseNotFoundError as error:
            self.session.rollback()
            self._block(task_id, "CONTRACT_PARSE_NOT_FOUND", str(error))
            raise

        rules = ReviewRuleRepository(self.session).list_active_rules()
        self.session.expunge(contract_parse)
        for rule in rules:
            self.session.expunge(rule)
        self.session.rollback()

        if current_status == TaskStatus.PARSING:
            TaskStateService(self.session).start_reviewing(task_id)

        try:
            matches = self.rule_engine.run(contract_parse, rules)
        except Exception as error:
            message = f"RULE_ENGINE_FAILED: {error}"
            self._block(task_id, "RULE_ENGINE_FAILED", message)
            raise RuleEngineFailedError(message) from error

        rule_by_id = {rule.id: rule for rule in rules}
        hits: list[RuleHit] = []
        created_count = 0
        reused_count = 0
        with self.session.begin():
            repository = RuleHitRepository(self.session)
            for match in matches:
                existing = repository.find_existing(
                    contract_parse.id, match.rule_id, match.rule_version
                )
                if existing is not None:
                    hits.append(existing)
                    reused_count += 1
                    continue
                hit = repository.add(
                    RuleHit(
                        contract_parse_id=contract_parse.id,
                        rule_id=match.rule_id,
                        rule_version=match.rule_version,
                        evidence_text=match.evidence_text,
                        evidence_position=match.evidence_position,
                        evidence_type=match.evidence_type,
                        actual_value=match.actual_value,
                        expected_value=match.expected_value,
                        hit_message=match.hit_message,
                        hit_status=RuleHitStatus.HIT,
                    )
                )
                hits.append(hit)
                created_count += 1
            log_type = "RULE_REVIEW_REUSED" if reused_count and not created_count else "RULE_REVIEW_COMPLETED"
            TaskRepository(self.session).add_log(
                task_id,
                "info",
                log_type,
                (
                    f"规则审查完成，ContractParse: {contract_parse.id}，"
                    f"新增命中: {created_count}，复用命中: {reused_count}"
                ),
            )

        hit_reads = [
            RuleHitRead.from_models(hit, rule_by_id[hit.rule_id]) for hit in hits
        ]
        return RuleReviewResponse(
            contract_parse_id=contract_parse.id,
            created_hit_count=created_count,
            reused_hit_count=reused_count,
            skipped_future_llm_count=sum(
                rule.match_mode == MatchMode.FUTURE_LLM for rule in rules
            ),
            summary=self._summarize(hit_reads),
            hits=hit_reads,
        )

    @staticmethod
    def _summarize(hits: list[RuleHitRead]) -> RuleReviewSummary:
        counts = {
            level: sum(hit.risk_level == level for hit in hits)
            for level in RiskLevel
        }
        if counts[RiskLevel.HIGH]:
            overall = RiskLevel.HIGH
        elif counts[RiskLevel.MEDIUM]:
            overall = RiskLevel.MEDIUM
        else:
            overall = RiskLevel.LOW
        return RuleReviewSummary(
            overall_risk_level=overall,
            hit_count=len(hits),
            high_count=counts[RiskLevel.HIGH],
            medium_count=counts[RiskLevel.MEDIUM],
            low_count=counts[RiskLevel.LOW],
            focus_points=[f"{hit.rule_name}：{hit.hit_message}" for hit in hits],
        )

    def _block(self, task_id: int, log_type: str, message: str) -> None:
        with self.session.begin():
            TaskRepository(self.session).add_log(
                task_id, "error", log_type, message
            )
        TaskStateService(self.session).block_task(
            task_id, message, stage="reviewing"
        )
