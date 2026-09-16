import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select

from app.models import (
    ContractParse,
    MatchMode,
    ReviewRule,
    RiskLevel,
    RuleHit,
    RuleStatus,
)
from app.rules import seed_default_review_rules
from tests.test_contract_extraction import create_task_with_snapshot


def create_parsed_task(client: TestClient) -> tuple[int, int]:
    task_id, _, _ = create_task_with_snapshot(client)
    response = client.post(f"/api/tasks/{task_id}/parse-contract")
    assert response.status_code == 200
    return task_id, response.json()["id"]


def update_parse_fact(
    client: TestClient,
    parse_id: int,
    section: str,
    field: str,
    **changes: object,
) -> None:
    with client.app.state.session_factory() as session:
        contract_parse = session.get(ContractParse, parse_id)
        assert contract_parse is not None
        column = "basic_info_json" if section == "basic_info" else "clause_info_json"
        values = dict(getattr(contract_parse, column))
        fact = dict(values[field])
        fact.update(changes)
        values[field] = fact
        setattr(contract_parse, column, values)
        session.commit()


def set_only_rules(client: TestClient, *rule_codes: str) -> None:
    with client.app.state.session_factory() as session:
        for rule in session.scalars(select(ReviewRule)):
            rule.rule_status = (
                RuleStatus.ACTIVE
                if rule.rule_code in rule_codes
                else RuleStatus.INACTIVE
            )
        session.commit()


def run_rules(client: TestClient, task_id: int) -> dict:
    response = client.post(f"/api/tasks/{task_id}/run-rules")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    ("section", "field", "rule_code"),
    [
        ("basic_info", "signing_party", "SUBJECT_INFO_MISSING"),
        ("basic_info", "amount", "AMOUNT_MISSING"),
        ("clauses", "confidentiality_clause", "CONFIDENTIALITY_MISSING"),
        ("clauses", "acceptance_clause", "ACCEPTANCE_STANDARD_MISSING"),
        ("clauses", "payment_clause", "PAYMENT_TERM_MISSING"),
    ],
)
def test_missing_fact_rules_hit(
    client: TestClient, section: str, field: str, rule_code: str
) -> None:
    task_id, parse_id = create_parsed_task(client)
    update_parse_fact(
        client,
        parse_id,
        section,
        field,
        value=None,
        source_text=None,
        position=None,
        extract_status="not_found",
    )

    result = run_rules(client, task_id)

    hit = next(item for item in result["hits"] if item["rule_code"] == rule_code)
    assert hit["evidence_type"] == "missing"
    assert hit["evidence_position"] is None


@pytest.mark.parametrize(
    ("payment_text", "expected_hit"),
    [("付款条款：预付款比例为75%。", True), ("付款条款：预付款比例为50%。", False)],
)
def test_prepayment_ratio_threshold(
    client: TestClient, payment_text: str, expected_hit: bool
) -> None:
    task_id, parse_id = create_parsed_task(client)
    update_parse_fact(
        client,
        parse_id,
        "clauses",
        "payment_clause",
        value=payment_text,
        source_text=payment_text,
    )

    codes = {item["rule_code"] for item in run_rules(client, task_id)["hits"]}

    assert ("PREPAYMENT_RATIO_HIGH" in codes) is expected_hit


def test_long_payment_period_hits(client: TestClient) -> None:
    task_id, parse_id = create_parsed_task(client)
    payment_text = "付款条款：收到发票后90天内支付款项。"
    update_parse_fact(
        client,
        parse_id,
        "clauses",
        "payment_clause",
        value=payment_text,
        source_text=payment_text,
    )

    hit = next(
        item
        for item in run_rules(client, task_id)["hits"]
        if item["rule_code"] == "PAYMENT_PERIOD_LONG"
    )

    assert hit["actual_value"] == "90天"
    assert hit["expected_value"] == "不高于 60天"


def test_auto_renewal_keyword_hits(client: TestClient) -> None:
    task_id, parse_id = create_parsed_task(client)
    text = "合同期满前双方未提出异议的，合同自动续约一年。"
    update_parse_fact(
        client,
        parse_id,
        "clauses",
        "breach_clause",
        value=text,
        source_text=text,
    )

    hit = next(
        item
        for item in run_rules(client, task_id)["hits"]
        if item["rule_code"] == "AUTO_RENEWAL_PRESENT"
    )
    assert hit["actual_value"] == "自动续约"
    assert hit["evidence_text"] == text


def test_inactive_rule_is_not_executed(client: TestClient) -> None:
    task_id, parse_id = create_parsed_task(client)
    update_parse_fact(
        client,
        parse_id,
        "basic_info",
        "amount",
        value=None,
        source_text=None,
        position=None,
        extract_status="not_found",
    )
    with client.app.state.session_factory() as session:
        rule = session.scalar(
            select(ReviewRule).where(ReviewRule.rule_code == "AMOUNT_MISSING")
        )
        assert rule is not None
        rule.rule_status = RuleStatus.INACTIVE
        session.commit()

    codes = {item["rule_code"] for item in run_rules(client, task_id)["hits"]}

    assert "AMOUNT_MISSING" not in codes


def test_future_llm_rule_is_skipped(client: TestClient) -> None:
    task_id, _ = create_parsed_task(client)
    with client.app.state.session_factory() as session:
        session.add(
            ReviewRule(
                rule_code="FUTURE_FAIRNESS_REVIEW",
                rule_name="未来公平性判断",
                risk_level=RiskLevel.HIGH,
                rule_status=RuleStatus.ACTIVE,
                match_mode=MatchMode.FUTURE_LLM,
                match_text=json.dumps({"prompt_key": "fairness"}),
                suggestion_text="等待未来 LLM 能力。",
                rule_version="1.0",
            )
        )
        session.commit()

    result = run_rules(client, task_id)

    assert result["skipped_future_llm_count"] == 1
    assert "FUTURE_FAIRNESS_REVIEW" not in {
        item["rule_code"] for item in result["hits"]
    }


def test_source_evidence_keeps_contract_parse_position(client: TestClient) -> None:
    task_id, parse_id = create_parsed_task(client)
    position = {"block_start": 7, "block_end": 8, "page_start": 1, "page_end": 2}
    text = "付款条款：预付款比例为75%。"
    update_parse_fact(
        client,
        parse_id,
        "clauses",
        "payment_clause",
        value=text,
        source_text=text,
        position=position,
    )

    hit = next(
        item
        for item in run_rules(client, task_id)["hits"]
        if item["rule_code"] == "PREPAYMENT_RATIO_HIGH"
    )

    assert hit["evidence_text"] == text
    assert hit["evidence_position"] == position
    assert hit["evidence_type"] == "derived"


def test_duplicate_run_reuses_hits_and_database_constraint(client: TestClient) -> None:
    task_id, parse_id = create_parsed_task(client)
    update_parse_fact(
        client,
        parse_id,
        "basic_info",
        "amount",
        value=None,
        source_text=None,
        position=None,
        extract_status="not_found",
    )

    first = run_rules(client, task_id)
    second = run_rules(client, task_id)

    assert second["created_hit_count"] == 0
    assert second["reused_hit_count"] == first["summary"]["hit_count"]
    with client.app.state.session_factory() as session:
        count = session.scalar(
            select(func.count()).select_from(RuleHit).where(
                RuleHit.contract_parse_id == parse_id
            )
        )
        constraints = inspect(session.get_bind()).get_unique_constraints("rule_hits")
    assert count == first["summary"]["hit_count"]
    assert ("contract_parse_id", "rule_id", "rule_version") in {
        tuple(item["column_names"]) for item in constraints
    }


def test_rule_version_change_creates_new_hit_and_keeps_history(
    client: TestClient,
) -> None:
    task_id, parse_id = create_parsed_task(client)
    set_only_rules(client, "DISPUTE_JURISDICTION_PRESENT")
    first = run_rules(client, task_id)
    with client.app.state.session_factory() as session:
        rule = session.scalar(
            select(ReviewRule).where(
                ReviewRule.rule_code == "DISPUTE_JURISDICTION_PRESENT"
            )
        )
        assert rule is not None
        rule.rule_version = "2.0"
        session.commit()

    second = run_rules(client, task_id)

    assert first["created_hit_count"] == 1
    assert second["created_hit_count"] == 1
    with client.app.state.session_factory() as session:
        versions = list(
            session.scalars(
                select(RuleHit.rule_version)
                .where(RuleHit.contract_parse_id == parse_id)
                .order_by(RuleHit.id)
            )
        )
    assert versions == ["1.0", "2.0"]


def test_high_hit_sets_overall_high(client: TestClient) -> None:
    task_id, parse_id = create_parsed_task(client)
    update_parse_fact(
        client,
        parse_id,
        "basic_info",
        "signing_party",
        value=None,
        source_text=None,
        position=None,
        extract_status="not_found",
    )
    assert run_rules(client, task_id)["summary"]["overall_risk_level"] == "high"


def test_only_medium_hit_sets_overall_medium(client: TestClient) -> None:
    task_id, parse_id = create_parsed_task(client)
    set_only_rules(client, "AMOUNT_MISSING")
    update_parse_fact(
        client,
        parse_id,
        "basic_info",
        "amount",
        value=None,
        source_text=None,
        position=None,
        extract_status="not_found",
    )
    assert run_rules(client, task_id)["summary"]["overall_risk_level"] == "medium"


def test_no_hit_is_valid_and_overall_low(client: TestClient) -> None:
    task_id, _ = create_parsed_task(client)
    set_only_rules(client, "AMOUNT_MISSING")

    result = run_rules(client, task_id)

    assert result["summary"] == {
        "overall_risk_level": "low",
        "hit_count": 0,
        "high_count": 0,
        "medium_count": 0,
        "low_count": 0,
        "focus_points": [],
    }


def test_missing_contract_parse_blocks_reviewing_stage(client: TestClient) -> None:
    task_id, _, _ = create_task_with_snapshot(client)

    response = client.post(f"/api/tasks/{task_id}/run-rules")

    assert response.status_code == 409
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["task_status"] == "blocked"
    assert task["blocked_stage"] == "reviewing"
    assert "CONTRACT_PARSE_NOT_FOUND" in task["blocked_reason"]


def test_rule_engine_exception_blocks_reviewing_stage(client: TestClient) -> None:
    class BrokenRuleEngine:
        name = "broken"
        version = "1"

        def run(self, contract_parse, active_rules):
            raise RuntimeError("模拟规则执行异常")

    task_id, _ = create_parsed_task(client)
    client.app.state.rule_engine = BrokenRuleEngine()

    response = client.post(f"/api/tasks/{task_id}/run-rules")

    assert response.status_code == 409
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["task_status"] == "blocked"
    assert task["blocked_stage"] == "reviewing"
    assert "RULE_ENGINE_FAILED" in task["blocked_reason"]


def test_successful_run_moves_parsing_to_reviewing_and_logs(client: TestClient) -> None:
    task_id, _ = create_parsed_task(client)

    run_rules(client, task_id)

    assert client.get(f"/api/tasks/{task_id}").json()["task_status"] == "reviewing"
    logs = client.get(f"/api/tasks/{task_id}/logs").json()
    assert any(log["log_type"] == "RULE_REVIEW_COMPLETED" for log in logs)
    assert any("parsing 变更为 reviewing" in log["log_content"] for log in logs)


def test_rule_hit_api_preserves_traceability(client: TestClient) -> None:
    task_id, parse_id = create_parsed_task(client)
    result = run_rules(client, task_id)

    response = client.get(f"/api/tasks/{task_id}/rule-hits")

    assert response.status_code == 200
    assert response.json() == result["hits"]
    with client.app.state.session_factory() as session:
        hit = session.get(RuleHit, response.json()[0]["id"])
        assert hit is not None
        assert hit.contract_parse_id == parse_id
        assert hit.contract_parse.document_read_snapshot.attachment.task_id == task_id


def test_review_rules_api_returns_idempotently_seeded_defaults(client: TestClient) -> None:
    first = client.get("/api/review-rules")
    second = client.get("/api/review-rules")

    assert first.status_code == 200
    assert len(first.json()) == 13
    assert second.json() == first.json()
    assert len({item["rule_code"] for item in first.json()}) == 13


def test_default_rule_seed_can_run_repeatedly(client: TestClient) -> None:
    with client.app.state.session_factory.begin() as session:
        first_created = seed_default_review_rules(session)
    with client.app.state.session_factory.begin() as session:
        second_created = seed_default_review_rules(session)
        count = session.scalar(select(func.count()).select_from(ReviewRule))

    assert first_created == 0
    assert second_created == 0
    assert count == 13


def test_dispute_jurisdiction_is_low_information_hit(client: TestClient) -> None:
    task_id, _ = create_parsed_task(client)
    set_only_rules(client, "DISPUTE_JURISDICTION_PRESENT")

    result = run_rules(client, task_id)
    hit = result["hits"][0]

    assert hit["risk_level"] == "low"
    assert hit["actual_value"] == "人民法院"
    assert result["summary"]["overall_risk_level"] == "low"
