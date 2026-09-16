from __future__ import annotations

from hashlib import sha256

from sqlalchemy.orm import Session

from app.core.task_state import InvalidTaskStateError
from app.integrations.llm import LLMProvider
from app.models import (
    EvidenceType,
    LlmRuleEvaluation,
    MatchMode,
    RiskLevel,
    RuleHit,
    RuleHitStatus,
    TaskStatus,
)
from app.repositories import (
    LlmRuleEvaluationRepository,
    ReviewRuleRepository,
    RuleHitRepository,
    TaskRepository,
)
from app.rules import (
    DeterministicRuleEngine,
    LlmSemanticRuleEngine,
    RuleEngine,
    rule_set_fingerprint,
)
from app.schemas import (
    LlmRuleDecision,
    RuleHitRead,
    RuleReviewResponse,
    RuleReviewSummary,
)
from app.services.contract_parse_selector import (
    ContractParseNotFoundError,
    ContractParseSelector,
)
from app.services.task_state_service import TaskNotFoundError, TaskStateService


class RuleEngineFailedError(RuntimeError):
    pass


class ContractReviewService:
    def __init__(
        self,
        session: Session,
        rule_engine: RuleEngine | None = None,
        llm_provider: LLMProvider | None = None,
    ) -> None:
        self.session = session
        self.rule_engine = rule_engine or DeterministicRuleEngine()
        self.semantic_engine = LlmSemanticRuleEngine(llm_provider) if llm_provider else None

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

        document_read = contract_parse.document_read_snapshot
        rules = ReviewRuleRepository(self.session).list_active_rules()
        self.session.expunge_all()
        self.session.rollback()
        if current_status == TaskStatus.PARSING:
            TaskStateService(self.session).start_reviewing(task_id)

        deterministic_rules = [
            rule for rule in rules if rule.match_mode != MatchMode.LLM_SEMANTIC
        ]
        semantic_rules = [
            rule for rule in rules if rule.match_mode == MatchMode.LLM_SEMANTIC
        ]
        try:
            matches = self.rule_engine.run(contract_parse, deterministic_rules)
        except Exception as error:
            message = f"RULE_ENGINE_FAILED: {error}"
            self._block(task_id, "RULE_ENGINE_FAILED", message)
            raise RuleEngineFailedError(message) from error

        rule_set_hash = rule_set_fingerprint(rules)
        hits, created_count, reused_count = self._save_deterministic_hits(
            contract_parse.id, matches
        )
        counts = {"evaluated": 0, "degraded": 0, "uncertain": 0, "reused": 0}
        for rule in semantic_rules:
            hit, event = self._run_semantic_rule(
                task_id, contract_parse, document_read, rule
            )
            counts[event] += 1
            if hit is not None:
                hits.append(hit)
                if event == "reused":
                    reused_count += 1
                else:
                    created_count += 1

        with self.session.begin():
            log_type = (
                "RULE_REVIEW_REUSED"
                if reused_count and not created_count
                else "RULE_REVIEW_COMPLETED"
            )
            TaskRepository(self.session).add_log(
                task_id, "info", log_type,
                f"规则审查完成，ContractParse: {contract_parse.id}，RuleSet: {rule_set_hash}，"
                f"新增命中: {created_count}，复用命中: {reused_count}，语义降级: {counts['degraded']}",
            )

        rule_by_id = {rule.id: rule for rule in rules}
        hits = sorted({hit.id: hit for hit in hits}.values(), key=lambda hit: hit.id)
        hit_reads = [RuleHitRead.from_models(hit, rule_by_id[hit.rule_id]) for hit in hits]
        return RuleReviewResponse(
            contract_parse_id=contract_parse.id,
            created_hit_count=created_count,
            reused_hit_count=reused_count,
            skipped_future_llm_count=sum(
                rule.match_mode == MatchMode.FUTURE_LLM for rule in rules
            ),
            semantic_evaluated_count=counts["evaluated"],
            semantic_degraded_count=counts["degraded"],
            semantic_uncertain_count=counts["uncertain"],
            semantic_reused_count=counts["reused"],
            summary=self._summarize(hit_reads),
            hits=hit_reads,
        )

    def _save_deterministic_hits(self, contract_parse_id, matches):
        hits: list[RuleHit] = []
        created_count = reused_count = 0
        with self.session.begin():
            repository = RuleHitRepository(self.session)
            for match in matches:
                existing = repository.find_existing(contract_parse_id, match.rule_id, match.rule_version)
                if existing:
                    hits.append(existing)
                    reused_count += 1
                    continue
                hits.append(repository.add(RuleHit(
                    contract_parse_id=contract_parse_id, rule_id=match.rule_id,
                    rule_version=match.rule_version, evidence_text=match.evidence_text,
                    evidence_position=match.evidence_position, evidence_type=match.evidence_type,
                    actual_value=match.actual_value, expected_value=match.expected_value,
                    hit_message=match.hit_message, hit_status=RuleHitStatus.HIT,
                )))
                created_count += 1
        return hits, created_count, reused_count

    def _run_semantic_rule(self, task_id, contract_parse, document_read, rule):
        fingerprint = self._semantic_fingerprint(rule)
        reusable = LlmRuleEvaluationRepository(self.session).find_reusable(
            contract_parse.id, rule.id, rule.rule_version, fingerprint
        )
        if reusable:
            hit = reusable.rule_hit
            evaluation_id = reusable.id
            self.session.expunge_all()
            self.session.rollback()
            with self.session.begin():
                TaskRepository(self.session).add_log(task_id, "info", "LLM_RULE_EVALUATION_REUSED", f"复用语义规则 {rule.rule_code} 的判断 {evaluation_id}")
            return hit, "reused"
        self.session.rollback()

        if self.semantic_engine is None:
            return self._save_degraded(task_id, contract_parse.id, rule, fingerprint, "LLM_PROVIDER_NOT_CONFIGURED", "未配置 LLM Provider")
        if not self.semantic_engine.clause_is_available(contract_parse, rule):
            with self.session.begin():
                evaluation = LlmRuleEvaluationRepository(self.session).add(LlmRuleEvaluation(
                    contract_parse_id=contract_parse.id, rule_id=rule.id, rule_version=rule.rule_version,
                    evaluation_fingerprint=fingerprint, decision=LlmRuleDecision.UNCERTAIN.value,
                    evaluation_status="skipped", reason="目标条款未提取，不重复执行缺失类风险判断",
                    evidence_position=None, metadata_json=self._metadata(rule, None),
                ))
                TaskRepository(self.session).add_log(task_id, "warning", "LLM_RULE_SKIPPED_NO_CLAUSE", f"语义规则 {rule.rule_code} 因目标条款不可用而跳过，Evaluation: {evaluation.id}")
            return None, "uncertain"

        try:
            outcome = self.semantic_engine.evaluate(contract_parse, document_read, rule)
        except Exception as error:
            return self._save_degraded(task_id, contract_parse.id, rule, fingerprint, type(error).__name__, str(error))

        status, decision, log_type = "success", outcome.decision, "LLM_RULE_NOT_HIT"
        if decision == LlmRuleDecision.HIT and not outcome.evidence_valid:
            status, decision, log_type = "rejected", LlmRuleDecision.UNCERTAIN, "LLM_RULE_EVIDENCE_INVALID"
        elif decision == LlmRuleDecision.UNCERTAIN:
            log_type = "LLM_RULE_UNCERTAIN"
        elif decision == LlmRuleDecision.HIT:
            log_type = "LLM_RULE_HIT_CREATED"

        hit = None
        with self.session.begin():
            if decision == LlmRuleDecision.HIT:
                semantic_version = f"{rule.rule_version}-{fingerprint[:12]}"
                hit = RuleHitRepository(self.session).find_existing(contract_parse.id, rule.id, semantic_version)
                if hit is None:
                    hit = RuleHitRepository(self.session).add(RuleHit(
                        contract_parse_id=contract_parse.id, rule_id=rule.id,
                        rule_version=semantic_version, evidence_text=outcome.evidence_text or "",
                        evidence_position=outcome.evidence_position, evidence_type=EvidenceType.SOURCE,
                        actual_value="semantic_hit", expected_value=None,
                        hit_message=outcome.reason, hit_status=RuleHitStatus.HIT,
                    ))
            evaluation = LlmRuleEvaluationRepository(self.session).add(LlmRuleEvaluation(
                contract_parse_id=contract_parse.id, rule_id=rule.id, rule_version=rule.rule_version,
                evaluation_fingerprint=fingerprint, decision=decision.value,
                evaluation_status=status, reason=outcome.reason,
                evidence_position=outcome.evidence_position if hit else None,
                metadata_json=self._metadata(rule, outcome.token_usage),
                rule_hit_id=hit.id if hit else None,
            ))
            TaskRepository(self.session).add_log(
                task_id, "warning" if status == "rejected" else "info", log_type,
                f"语义规则 {rule.rule_code} 判断为 {decision.value}，Evaluation: {evaluation.id}",
            )
        return hit, "uncertain" if decision == LlmRuleDecision.UNCERTAIN else "evaluated"

    def _save_degraded(self, task_id, contract_parse_id, rule, fingerprint, error_type, error_message):
        metadata = self._metadata(rule, None)
        metadata.update({"error_type": error_type, "error_message": error_message})
        with self.session.begin():
            evaluation = LlmRuleEvaluationRepository(self.session).add(LlmRuleEvaluation(
                contract_parse_id=contract_parse_id, rule_id=rule.id, rule_version=rule.rule_version,
                evaluation_fingerprint=fingerprint, decision=None, evaluation_status="degraded",
                reason=None, evidence_position=None, metadata_json=metadata,
            ))
            TaskRepository(self.session).add_log(task_id, "warning", "LLM_RULE_EVALUATION_DEGRADED", f"语义规则 {rule.rule_code} 降级，Evaluation: {evaluation.id}，{error_type}: {error_message}")
        return None, "degraded"

    def _semantic_fingerprint(self, rule) -> str:
        if self.semantic_engine:
            return self.semantic_engine.evaluation_fingerprint(rule)
        return sha256(f"{rule.rule_version}|1.0|not_configured|not_configured".encode()).hexdigest()

    def _metadata(self, rule, token_usage):
        return {
            "provider": self.semantic_engine.provider.provider_name if self.semantic_engine else "not_configured",
            "model": self.semantic_engine.provider.model_name if self.semantic_engine else "not_configured",
            "evaluator_version": self.semantic_engine.version if self.semantic_engine else "1.0",
            "rule_code": rule.rule_code,
            "token_usage": token_usage or {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
        }

    @staticmethod
    def _summarize(hits: list[RuleHitRead]) -> RuleReviewSummary:
        counts = {level: sum(hit.risk_level == level for hit in hits) for level in RiskLevel}
        overall = RiskLevel.HIGH if counts[RiskLevel.HIGH] else RiskLevel.MEDIUM if counts[RiskLevel.MEDIUM] else RiskLevel.LOW
        return RuleReviewSummary(overall_risk_level=overall, hit_count=len(hits), high_count=counts[RiskLevel.HIGH], medium_count=counts[RiskLevel.MEDIUM], low_count=counts[RiskLevel.LOW], focus_points=[f"{hit.rule_name}：{hit.hit_message}" for hit in hits])

    def _block(self, task_id: int, log_type: str, message: str) -> None:
        with self.session.begin():
            TaskRepository(self.session).add_log(task_id, "error", log_type, message)
        TaskStateService(self.session).block_task(task_id, message, stage="reviewing")
