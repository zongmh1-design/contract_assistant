import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.integrations.llm import MockLLMProvider
from app.models import ContractParse, LlmRuleEvaluation, ReviewRule, RuleHit
from app.rules import LlmSemanticRuleEngine
from tests.test_contract_review import create_parsed_task, run_rules, set_only_rules, update_parse_fact


SEMANTIC_RULE = "BREACH_LIABILITY_IMBALANCE"


def _breach_range(client: TestClient, parse_id: int) -> tuple[int, int]:
    with client.app.state.session_factory() as session:
        contract_parse = session.get(ContractParse, parse_id)
        assert contract_parse is not None
        position = contract_parse.clause_info_json["breach_clause"]["position"]
        return position["block_start"], position["block_end"]


def _run_with_response(
    client: TestClient,
    response: dict,
    *,
    model: str = "semantic-model-1",
    failure_mode: str | None = None,
) -> tuple[int, int, dict, MockLLMProvider]:
    task_id, parse_id = create_parsed_task(client)
    set_only_rules(client, SEMANTIC_RULE)
    provider = MockLLMProvider(response, model_name=model, failure_mode=failure_mode)
    client.app.state.llm_provider = provider
    return task_id, parse_id, run_rules(client, task_id), provider


def test_semantic_hit_uses_verified_source_evidence_and_rule_configuration(client: TestClient) -> None:
    task_id, parse_id = create_parsed_task(client)
    set_only_rules(client, SEMANTIC_RULE)
    start, end = _breach_range(client, parse_id)
    reason = "违约责任只约束一方，需要人工确认"
    provider = MockLLMProvider({"decision": "hit", "reason": reason, "block_start": start, "block_end": end})
    client.app.state.llm_provider = provider

    result = run_rules(client, task_id)

    assert result["semantic_evaluated_count"] == 1
    hit = result["hits"][0]
    assert hit["rule_code"] == SEMANTIC_RULE
    assert hit["risk_level"] == "high"
    assert hit["suggestion_text"] == "建议人工核对双方违约责任、责任上限和免责范围是否对等。"
    assert hit["hit_message"] == reason
    assert reason not in hit["evidence_text"]
    assert "违约责任" in hit["evidence_text"]
    assert hit["evidence_position"]["block_start"] == start
    assert hit["evidence_position"]["page_start"] == 2
    assert "document_blocks" in provider.last_user_prompt
    assert "付款条款" not in provider.last_user_prompt


@pytest.mark.parametrize("decision", ["not_hit", "uncertain"])
def test_not_hit_and_uncertain_do_not_create_rule_hit(client: TestClient, decision: str) -> None:
    task_id, parse_id, result, _ = _run_with_response(
        client, {"decision": decision, "reason": "没有足够的明确风险线索"}
    )
    assert result["hits"] == []
    assert result["semantic_uncertain_count"] == (1 if decision == "uncertain" else 0)
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RuleHit).where(RuleHit.contract_parse_id == parse_id)) == 0


def test_hit_with_invalid_or_hallucinated_block_is_rejected(client: TestClient) -> None:
    task_id, parse_id, result, _ = _run_with_response(
        client,
        {"decision": "hit", "reason": "模型声称存在风险", "block_start": 999, "block_end": 999},
    )
    assert result["hits"] == []
    assert result["semantic_uncertain_count"] == 1
    logs = client.get(f"/api/tasks/{task_id}/logs").json()
    assert any(log["log_type"] == "LLM_RULE_EVIDENCE_INVALID" for log in logs)
    with client.app.state.session_factory() as session:
        evaluation = session.scalar(select(LlmRuleEvaluation).where(LlmRuleEvaluation.contract_parse_id == parse_id))
        assert evaluation.evaluation_status == "rejected"
        assert evaluation.decision == "uncertain"


@pytest.mark.parametrize("failure_mode", ["timeout", "provider_error", "invalid_schema"])
def test_provider_failure_degrades_without_blocking_task(client: TestClient, failure_mode: str) -> None:
    task_id, parse_id, result, _ = _run_with_response(client, {}, failure_mode=failure_mode)
    assert result["semantic_degraded_count"] == 1
    assert client.get(f"/api/tasks/{task_id}").json()["task_status"] == "reviewing"
    logs = client.get(f"/api/tasks/{task_id}/logs").json()
    assert any(log["log_type"] == "LLM_RULE_EVALUATION_DEGRADED" for log in logs)
    with client.app.state.session_factory() as session:
        evaluation = session.scalar(select(LlmRuleEvaluation).where(LlmRuleEvaluation.contract_parse_id == parse_id))
        assert evaluation.metadata_json["provider"] == "mock"
        assert evaluation.metadata_json["token_usage"]["total_tokens"] is None


def test_one_semantic_failure_does_not_remove_deterministic_hits(client: TestClient) -> None:
    task_id, parse_id = create_parsed_task(client)
    set_only_rules(client, "AMOUNT_MISSING", SEMANTIC_RULE)
    update_parse_fact(client, parse_id, "basic_info", "amount", value=None, source_text=None, position=None, extract_status="not_found")
    client.app.state.llm_provider = MockLLMProvider({}, failure_mode="timeout")

    result = run_rules(client, task_id)

    assert {hit["rule_code"] for hit in result["hits"]} == {"AMOUNT_MISSING"}
    assert result["semantic_degraded_count"] == 1


def test_semantic_evaluation_is_reused_without_second_provider_call(client: TestClient) -> None:
    task_id, parse_id = create_parsed_task(client)
    set_only_rules(client, SEMANTIC_RULE)
    start, end = _breach_range(client, parse_id)
    provider = MockLLMProvider({"decision": "hit", "reason": "明显单方责任", "block_start": start, "block_end": end})
    client.app.state.llm_provider = provider

    first = run_rules(client, task_id)
    second = run_rules(client, task_id)

    assert provider.calls == 1
    assert second["semantic_reused_count"] == 1
    assert second["hits"][0]["id"] == first["hits"][0]["id"]
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(LlmRuleEvaluation).where(LlmRuleEvaluation.contract_parse_id == parse_id)) == 1


def test_model_change_runs_again_and_latest_not_hit_supersedes_old_hit(client: TestClient) -> None:
    task_id, parse_id = create_parsed_task(client)
    set_only_rules(client, SEMANTIC_RULE)
    start, end = _breach_range(client, parse_id)
    client.app.state.llm_provider = MockLLMProvider({"decision": "hit", "reason": "明显单方责任", "block_start": start, "block_end": end}, model_name="model-v1")
    first = run_rules(client, task_id)
    client.app.state.llm_provider = MockLLMProvider({"decision": "not_hit", "reason": "双方责任基本对等"}, model_name="model-v2")

    second = run_rules(client, task_id)

    assert first["summary"]["hit_count"] == 1
    assert second["summary"]["hit_count"] == 0
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(LlmRuleEvaluation).where(LlmRuleEvaluation.contract_parse_id == parse_id)) == 2
        assert session.scalar(select(func.count()).select_from(RuleHit).where(RuleHit.contract_parse_id == parse_id)) == 1


def test_evaluator_version_change_runs_again(client: TestClient, monkeypatch) -> None:
    task_id, parse_id = create_parsed_task(client)
    set_only_rules(client, SEMANTIC_RULE)
    provider = MockLLMProvider({"decision": "not_hit", "reason": "未发现明确失衡"})
    client.app.state.llm_provider = provider
    run_rules(client, task_id)
    monkeypatch.setattr(LlmSemanticRuleEngine, "version", "2.0")

    run_rules(client, task_id)

    assert provider.calls == 2
    with client.app.state.session_factory() as session:
        evaluations = list(session.scalars(select(LlmRuleEvaluation).where(LlmRuleEvaluation.contract_parse_id == parse_id)))
        assert {item.metadata_json["evaluator_version"] for item in evaluations} == {"1.0", "2.0"}


def test_review_result_combines_deterministic_and_semantic_hits(client: TestClient) -> None:
    task_id, parse_id = create_parsed_task(client)
    set_only_rules(client, "DISPUTE_JURISDICTION_PRESENT", SEMANTIC_RULE)
    start, end = _breach_range(client, parse_id)
    client.app.state.llm_provider = MockLLMProvider({"decision": "hit", "reason": "明显单方责任", "block_start": start, "block_end": end})
    review = run_rules(client, task_id)

    response = client.post(f"/api/tasks/{task_id}/review-results")

    assert response.status_code == 200
    result = response.json()
    assert result["review_status"] == "completed"
    assert result["overall_risk_level"] == "high"
    assert len(result["rule_hit_ids"]) == 2
    assert {hit["rule_code"] for hit in review["hits"]} == {"DISPUTE_JURISDICTION_PRESENT", SEMANTIC_RULE}


def test_degraded_semantic_review_creates_partial_review_result(client: TestClient) -> None:
    task_id, _, _, _ = _run_with_response(client, {}, failure_mode="timeout")
    result = client.post(f"/api/tasks/{task_id}/review-results").json()
    assert result["review_status"] == "partial"
    assert "部分 LLM 语义规则未完成" in result["summary_text"]
