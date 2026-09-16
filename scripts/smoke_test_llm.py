from __future__ import annotations

import json
from time import perf_counter

from app.integrations.llm import create_llm_provider_from_environment
from app.models import (
    ContractParse,
    DocumentReadSnapshot,
    MatchMode,
    ReviewRule,
    RiskLevel,
    RuleStatus,
)
from app.parsers import DeterministicContractExtractor, LlmContractExtractor
from app.rules import LlmSemanticRuleEngine
from app.rules.default_rules import DEFAULT_REVIEW_RULES
from app.schemas import LlmRuleDecision
from app.services.contract_extraction_service import ContractExtractionService


def _snapshot(blocks: list[str]) -> DocumentReadSnapshot:
    block_values = [
        {"block_index": index, "page_number": 1, "text": text}
        for index, text in enumerate(blocks)
    ]
    return DocumentReadSnapshot(
        id=1,
        attachment_id=1,
        read_method="smoke_fixture",
        file_sha256="0" * 64,
        file_type="txt",
        text="\n".join(blocks),
        blocks_json=block_values,
        page_count=1,
        requires_ocr=False,
        read_status="success",
        reader_version="smoke-1",
    )


def _usage_text(value: int | None) -> str:
    return str(value) if value is not None else "unavailable"


def _field_extraction_smoke(provider) -> None:
    snapshot = _snapshot(
        [
            "软件服务采购合同",
            "本项目内部标识为 SMOKE-2026-A。",
            "委托采购主体为星云实验室（虚构），受托服务主体为蓝桥技术工作室（虚构）。",
            "双方确认本项目服务费为人民币壹万元整。",
        ]
    )
    deterministic = DeterministicContractExtractor().extract(snapshot)
    started = perf_counter()
    outcome = LlmContractExtractor(provider).enhance(snapshot, deterministic)
    duration = perf_counter() - started
    resolved = outcome.metadata["resolved_fields"]
    if not resolved:
        raise RuntimeError("字段提取 smoke test 未补充任何 unresolved 字段")

    hybrid = ContractParse(
        document_read_snapshot_id=1,
        basic_info_json=outcome.extraction.basic_info.model_dump(mode="json"),
        clause_info_json=outcome.extraction.clauses.model_dump(mode="json"),
        parse_status=ContractExtractionService.status_for(outcome.extraction),
        parse_error=None,
        extractor_name="hybrid_contract_extractor",
        extractor_version=LlmContractExtractor.version,
        llm_metadata_json=outcome.metadata,
    )
    first_field = resolved[0]
    container = (
        hybrid.basic_info_json
        if first_field in hybrid.basic_info_json
        else hybrid.clause_info_json
    )
    fact = container[first_field]
    if fact["extract_status"] != "found" or not fact["source_text"]:
        raise RuntimeError("字段提取结果未通过本地证据验证")
    usage = outcome.metadata["token_usage"]
    print("\n[场景 A] 辅助字段提取")
    print(f"request_duration_seconds: {duration:.3f}")
    print(f"extraction_result: {first_field}={fact['value']}")
    print(f"evidence_block: {fact['position']}")
    print(f"evidence_text: {fact['source_text']}")
    print("validation_result: passed")
    print(f"prompt_tokens: {_usage_text(usage['prompt_tokens'])}")
    print(f"completion_tokens: {_usage_text(usage['completion_tokens'])}")
    print(f"total_tokens: {_usage_text(usage['total_tokens'])}")


def _semantic_rule_smoke(provider) -> None:
    snapshot = _snapshot(
        [
            "第六条 违约责任",
            "乙方任何违约均应承担全部损失及高额违约金；甲方违反任何义务均不承担责任。",
        ]
    )
    extraction = DeterministicContractExtractor().extract(snapshot)
    contract_parse = ContractParse(
        id=1,
        document_read_snapshot_id=1,
        basic_info_json=extraction.basic_info.model_dump(mode="json"),
        clause_info_json=extraction.clauses.model_dump(mode="json"),
        parse_status=ContractExtractionService.status_for(extraction),
        parse_error=None,
        extractor_name="deterministic_contract_extractor",
        extractor_version=DeterministicContractExtractor.version,
    )
    definition = next(
        item
        for item in DEFAULT_REVIEW_RULES
        if item["rule_code"] == "BREACH_LIABILITY_IMBALANCE"
    )
    rule = ReviewRule(
        id=1,
        **definition,
        rule_status=RuleStatus.ACTIVE,
        rule_version="1.0",
    )
    if rule.match_mode != MatchMode.LLM_SEMANTIC:
        raise RuntimeError("Smoke rule 配置错误")

    started = perf_counter()
    outcome = LlmSemanticRuleEngine(provider).evaluate(
        contract_parse, snapshot, rule
    )
    duration = perf_counter() - started
    if outcome.decision != LlmRuleDecision.HIT or not outcome.evidence_valid:
        raise RuntimeError(
            f"明确失衡条款未得到带合法证据的 hit: {outcome.decision.value}"
        )
    print("\n[场景 B] LLM 语义规则")
    print(f"request_duration_seconds: {duration:.3f}")
    print(f"decision: {outcome.decision.value}")
    print(f"risk_level_from_review_rule: {rule.risk_level.value}")
    print(f"reason: {outcome.reason}")
    print(f"evidence_block: {json.dumps(outcome.evidence_position, ensure_ascii=False)}")
    print(f"evidence_text: {outcome.evidence_text}")
    print("validation_result: passed")
    print(f"prompt_tokens: {_usage_text(outcome.token_usage['prompt_tokens'])}")
    print(f"completion_tokens: {_usage_text(outcome.token_usage['completion_tokens'])}")
    print(f"total_tokens: {_usage_text(outcome.token_usage['total_tokens'])}")


def main() -> None:
    provider = create_llm_provider_from_environment()
    if provider is None:
        raise SystemExit(
            "真实 LLM smoke test 未执行：请先配置 LLM_BASE_URL、LLM_API_KEY 和 LLM_MODEL。"
        )
    print("=== Real LLM Provider Smoke Test ===")
    print(f"provider: {provider.provider_name}")
    print(f"model: {provider.model_name}")
    _field_extraction_smoke(provider)
    _semantic_rule_smoke(provider)
    print("\nSmoke test result: passed")


if __name__ == "__main__":
    main()
