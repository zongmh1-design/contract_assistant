from dataclasses import replace

from sqlalchemy.orm import Session

from app.core.task_state import InvalidTaskStateError
from app.models import ReviewResult, ReviewStatus, RiskLevel, RuleHit, TaskStatus
from app.repositories import (
    ReviewResultRepository,
    LlmRuleEvaluationRepository,
    ReviewRuleRepository,
    RuleHitRepository,
    TaskRepository,
)
from app.rules import rule_hit_fingerprint, rule_set_fingerprint
from app.schemas import ReviewResultRead
from app.services.contract_parse_selector import (
    ContractParseNotFoundError,
    ContractParseSelector,
)
from app.services.review_result_builder import (
    DeterministicReviewResultBuilder,
    ReviewResultBuilder,
)
from app.services.task_state_service import TaskNotFoundError, TaskStateService


class RuleReviewNotFoundError(LookupError):
    pass


class ReviewResultGenerationError(RuntimeError):
    pass


class ReviewResultService:
    def __init__(
        self,
        session: Session,
        builder: ReviewResultBuilder | None = None,
    ) -> None:
        self.session = session
        self.builder = builder or DeterministicReviewResultBuilder()

    def generate(self, task_id: int) -> ReviewResultRead:
        task = TaskRepository(self.session).get_task(task_id)
        if task is None:
            self.session.rollback()
            raise TaskNotFoundError(f"任务不存在: {task_id}")
        if task.task_status not in {TaskStatus.PARSING, TaskStatus.REVIEWING}:
            current_status = task.task_status.value
            self.session.rollback()
            raise InvalidTaskStateError(
                f"任务处于 {current_status}，不能生成最终审查结果"
            )

        try:
            contract_parse = ContractParseSelector(self.session).select_for_task(task_id)
        except ContractParseNotFoundError as error:
            self.session.rollback()
            self._block(task_id, "CONTRACT_PARSE_NOT_FOUND", str(error))
            raise

        rules = ReviewRuleRepository(self.session).list_active_rules()
        current_rule_set = rule_set_fingerprint(rules)
        hits = RuleHitRepository(self.session).list_current_for_parse(contract_parse.id)
        semantic_rules = [rule for rule in rules if rule.match_mode.value == "llm_semantic"]
        latest_semantic = LlmRuleEvaluationRepository(
            self.session
        ).latest_for_active_semantic_rules(contract_parse.id)
        has_review = TaskRepository(self.session).has_rule_review_checkpoint(
            task_id, contract_parse.id, current_rule_set
        )
        if task.task_status != TaskStatus.REVIEWING or not has_review:
            self.session.rollback()
            message = (
                "RULE_REVIEW_NOT_FOUND: 当前 ContractParse 与 active 规则版本"
                "没有已完成的规则审查"
            )
            self._block(task_id, "RULE_REVIEW_NOT_FOUND", message)
            raise RuleReviewNotFoundError(message)

        semantic_evaluations = list(latest_semantic.values())
        current_hit_set = rule_hit_fingerprint(hits, semantic_evaluations)
        semantic_partial = any(
            rule.id not in latest_semantic
            or latest_semantic[rule.id].evaluation_status != "success"
            or latest_semantic[rule.id].decision == "uncertain"
            for rule in semantic_rules
        )
        self.session.expunge_all()
        self.session.rollback()

        repository = ReviewResultRepository(self.session)
        reusable = repository.find_reusable(
            contract_parse.id,
            current_rule_set,
            current_hit_set,
            self.builder.version,
        )
        if reusable is not None:
            response = ReviewResultRead.from_model(reusable)
            self.session.expunge_all()
            self.session.rollback()
            with self.session.begin():
                TaskRepository(self.session).add_log(
                    task_id,
                    "info",
                    "REVIEW_RESULT_REUSED",
                    f"复用最终审查结果 {reusable.id}，ContractParse: {contract_parse.id}",
                )
            return response
        self.session.rollback()

        try:
            built = self.builder.build(hits)
            if semantic_partial:
                note = "部分 LLM 语义规则未完成或无法确定，确定性审查结果仍然有效。"
                built = replace(
                    built,
                    summary_text=f"{built.summary_text}{note}",
                    comment_text=f"{built.comment_text}\n\n提示：{note}",
                    review_status=ReviewStatus.PARTIAL,
                )
        except Exception as error:
            return self._save_failed_and_block(
                task_id,
                contract_parse.id,
                hits,
                current_rule_set,
                current_hit_set,
                error,
            )

        with self.session.begin():
            managed_hits = [self.session.get(RuleHit, hit.id) for hit in hits]
            result = repository.add(
                ReviewResult(
                    contract_parse_id=contract_parse.id,
                    overall_risk_level=built.overall_risk_level,
                    summary_text=built.summary_text,
                    focus_points_json=built.focus_points,
                    comment_text=built.comment_text,
                    review_status=built.review_status,
                    review_error=None,
                    review_version=self.builder.version,
                    rule_set_fingerprint=current_rule_set,
                    rule_hit_fingerprint=current_hit_set,
                    rule_hits=[hit for hit in managed_hits if hit is not None],
                )
            )
            TaskRepository(self.session).add_log(
                task_id,
                "info",
                "REVIEW_RESULT_CREATED",
                (
                    f"生成最终审查结果 {result.id}，ContractParse: "
                    f"{contract_parse.id}，风险等级: {built.overall_risk_level.value}"
                ),
            )
        return ReviewResultRead.from_model(result)

    def _save_failed_and_block(
        self,
        task_id: int,
        contract_parse_id: int,
        hits: list[RuleHit],
        rule_set_hash: str,
        rule_hit_hash: str,
        error: Exception,
    ) -> ReviewResultRead:
        message = f"REVIEW_RESULT_GENERATION_FAILED: {error}"
        with self.session.begin():
            managed_hits = [self.session.get(RuleHit, hit.id) for hit in hits]
            result = ReviewResultRepository(self.session).add(
                ReviewResult(
                    contract_parse_id=contract_parse_id,
                    overall_risk_level=self._risk_from_hits(hits),
                    summary_text="",
                    focus_points_json=[],
                    comment_text="",
                    review_status=ReviewStatus.FAILED,
                    review_error=message,
                    review_version=self.builder.version,
                    rule_set_fingerprint=rule_set_hash,
                    rule_hit_fingerprint=rule_hit_hash,
                    rule_hits=[hit for hit in managed_hits if hit is not None],
                )
            )
            TaskRepository(self.session).add_log(
                task_id,
                "error",
                "REVIEW_RESULT_GENERATION_FAILED",
                f"{message}，ReviewResult: {result.id}",
            )
        TaskStateService(self.session).block_task(task_id, message, stage="reviewing")
        raise ReviewResultGenerationError(message)

    @staticmethod
    def _risk_from_hits(hits: list[RuleHit]) -> RiskLevel:
        levels = {hit.rule.risk_level for hit in hits}
        if RiskLevel.HIGH in levels:
            return RiskLevel.HIGH
        if RiskLevel.MEDIUM in levels:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def _block(self, task_id: int, log_type: str, message: str) -> None:
        with self.session.begin():
            TaskRepository(self.session).add_log(task_id, "error", log_type, message)
        TaskStateService(self.session).block_task(task_id, message, stage="reviewing")
