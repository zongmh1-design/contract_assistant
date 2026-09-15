from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import (
    EvidenceType,
    ReviewResult,
    ReviewRule,
    RiskLevel,
    RuleHit,
    RuleHitStatus,
    RuleStatus,
)
from app.services.review_result_builder import DeterministicReviewResultBuilder
from app.services.task_state_service import TaskStateService
from tests.test_contract_extraction import create_task_with_snapshot
from tests.test_contract_review import (
    create_parsed_task,
    run_rules,
    set_only_rules,
    update_parse_fact,
)


def create_reviewed_task(
    client: TestClient,
    *,
    active_rules: tuple[str, ...] = ("DISPUTE_JURISDICTION_PRESENT",),
) -> tuple[int, int, dict]:
    task_id, parse_id = create_parsed_task(client)
    set_only_rules(client, *active_rules)
    rule_review = run_rules(client, task_id)
    return task_id, parse_id, rule_review


def create_result(client: TestClient, task_id: int) -> dict:
    response = client.post(f"/api/tasks/{task_id}/review-results")
    assert response.status_code == 200, response.text
    return response.json()


def mark_fact_missing(
    client: TestClient, parse_id: int, section: str, field: str
) -> None:
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


def test_high_hit_generates_high_review_result(client: TestClient) -> None:
    task_id, parse_id = create_parsed_task(client)
    set_only_rules(client, "SUBJECT_INFO_MISSING")
    mark_fact_missing(client, parse_id, "basic_info", "signing_party")
    run_rules(client, task_id)

    result = create_result(client, task_id)

    assert result["overall_risk_level"] == "high"
    assert result["review_status"] == "completed"


def test_only_medium_hit_generates_medium_review_result(client: TestClient) -> None:
    task_id, parse_id = create_parsed_task(client)
    set_only_rules(client, "AMOUNT_MISSING")
    mark_fact_missing(client, parse_id, "basic_info", "amount")
    run_rules(client, task_id)

    assert create_result(client, task_id)["overall_risk_level"] == "medium"


def test_zero_hit_generates_low_completed_result(client: TestClient) -> None:
    task_id, _, review = create_reviewed_task(
        client, active_rules=("AMOUNT_MISSING",)
    )
    assert review["summary"]["hit_count"] == 0

    result = create_result(client, task_id)

    assert result["overall_risk_level"] == "low"
    assert result["review_status"] == "completed"
    assert result["rule_hit_ids"] == []
    assert client.get(f"/api/tasks/{task_id}").json()["task_status"] == "reviewing"


def test_summary_text_uses_actual_hit_counts_and_names(client: TestClient) -> None:
    task_id, parse_id = create_parsed_task(client)
    set_only_rules(client, "SUBJECT_INFO_MISSING", "AMOUNT_MISSING")
    mark_fact_missing(client, parse_id, "basic_info", "signing_party")
    mark_fact_missing(client, parse_id, "basic_info", "amount")
    run_rules(client, task_id)

    summary = create_result(client, task_id)["summary_text"]

    assert "共命中 2 条风险规则" in summary
    assert "高风险 1 条、中风险 1 条、低风险 0 条" in summary
    assert "合同主体信息缺失" in summary


def test_focus_points_contain_rule_and_suggestion(client: TestClient) -> None:
    task_id, _, _ = create_reviewed_task(client)

    point = create_result(client, task_id)["focus_points_json"][0]

    assert point["rule_code"] == "DISPUTE_JURISDICTION_PRESENT"
    assert point["rule_name"] == "争议管辖信息已识别"
    assert point["risk_level"] == "low"
    assert point["message"]
    assert point["suggestion"]


def test_focus_point_preserves_evidence_text_and_position(client: TestClient) -> None:
    task_id, _, review = create_reviewed_task(client)
    source_hit = review["hits"][0]

    point = create_result(client, task_id)["focus_points_json"][0]

    assert point["evidence_type"] == source_hit["evidence_type"]
    assert point["evidence_text"] == source_hit["evidence_text"]
    assert point["evidence_position"] == source_hit["evidence_position"]


def test_comment_text_is_deterministic_draft(client: TestClient) -> None:
    task_id, _, _ = create_reviewed_task(client)

    comment = create_result(client, task_id)["comment_text"]

    assert comment.startswith("合同审查结果：低风险")
    assert "重点关注：" in comment
    assert "建议：" in comment
    assert "争议管辖信息已识别" in comment


def test_review_result_relates_to_contract_parse(client: TestClient) -> None:
    task_id, parse_id, _ = create_reviewed_task(client)

    result = create_result(client, task_id)

    assert result["contract_parse_id"] == parse_id
    with client.app.state.session_factory() as session:
        stored = session.get(ReviewResult, result["id"])
        assert stored is not None
        assert (
            stored.contract_parse.document_read_snapshot.attachment.task_id == task_id
        )


def test_review_result_association_records_used_rule_hits(client: TestClient) -> None:
    task_id, _, review = create_reviewed_task(client)

    result = create_result(client, task_id)

    assert result["rule_hit_ids"] == [item["id"] for item in review["hits"]]
    with client.app.state.session_factory() as session:
        stored = session.get(ReviewResult, result["id"])
        assert stored is not None
        assert [hit.id for hit in stored.rule_hits] == result["rule_hit_ids"]


def test_duplicate_generation_reuses_review_result(client: TestClient) -> None:
    task_id, _, _ = create_reviewed_task(client)

    first = create_result(client, task_id)
    second = create_result(client, task_id)

    assert second["id"] == first["id"]
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ReviewResult)) == 1
    logs = client.get(f"/api/tasks/{task_id}/logs").json()
    assert any(log["log_type"] == "REVIEW_RESULT_REUSED" for log in logs)


def test_rule_hit_set_change_creates_new_result(client: TestClient) -> None:
    task_id, parse_id, _ = create_reviewed_task(client)
    first = create_result(client, task_id)
    with client.app.state.session_factory() as session:
        auto_rule = session.scalar(
            select(ReviewRule).where(ReviewRule.rule_code == "AUTO_RENEWAL_PRESENT")
        )
        assert auto_rule is not None
        auto_rule.rule_status = RuleStatus.ACTIVE
        session.add(
            RuleHit(
                contract_parse_id=parse_id,
                rule_id=auto_rule.id,
                rule_version=auto_rule.rule_version,
                evidence_text="合同到期后自动续约一年。",
                evidence_position={
                    "block_start": 20,
                    "block_end": 20,
                    "page_start": 2,
                    "page_end": 2,
                },
                evidence_type=EvidenceType.SOURCE,
                actual_value="自动续约",
                expected_value=None,
                hit_message="发现自动续约表达",
                hit_status=RuleHitStatus.HIT,
            )
        )
        session.commit()
    run_rules(client, task_id)

    second = create_result(client, task_id)

    assert second["id"] != first["id"]
    assert second["rule_hit_fingerprint"] != first["rule_hit_fingerprint"]


def test_review_version_change_creates_new_result(client: TestClient) -> None:
    class VersionTwoBuilder(DeterministicReviewResultBuilder):
        version = "2.0"

    task_id, _, _ = create_reviewed_task(client)
    first = create_result(client, task_id)
    client.app.state.review_result_builder = VersionTwoBuilder()

    second = create_result(client, task_id)

    assert second["id"] != first["id"]
    assert second["review_version"] == "2.0"


def test_rule_version_change_requires_rerun_and_creates_history(
    client: TestClient,
) -> None:
    task_id, _, _ = create_reviewed_task(client)
    first = create_result(client, task_id)
    with client.app.state.session_factory() as session:
        rule = session.scalar(
            select(ReviewRule).where(
                ReviewRule.rule_code == "DISPUTE_JURISDICTION_PRESENT"
            )
        )
        assert rule is not None
        rule.rule_version = "2.0"
        session.commit()

    missing_review = client.post(f"/api/tasks/{task_id}/review-results")

    assert missing_review.status_code == 409
    assert "RULE_REVIEW_NOT_FOUND" in missing_review.json()["detail"]
    with client.app.state.session_factory() as session:
        TaskStateService(session).retry_task(task_id)
    run_rules(client, task_id)

    second = create_result(client, task_id)
    results = client.get(f"/api/tasks/{task_id}/review-results").json()

    assert second["id"] != first["id"]
    assert second["rule_set_fingerprint"] != first["rule_set_fingerprint"]
    assert [item["id"] for item in results] == [first["id"], second["id"]]


def test_old_review_results_are_retained(client: TestClient) -> None:
    class VersionTwoBuilder(DeterministicReviewResultBuilder):
        version = "2.0"

    task_id, _, _ = create_reviewed_task(client)
    first = create_result(client, task_id)
    client.app.state.review_result_builder = VersionTwoBuilder()
    second = create_result(client, task_id)

    results = client.get(f"/api/tasks/{task_id}/review-results").json()

    assert [item["id"] for item in results] == [first["id"], second["id"]]


def test_missing_contract_parse_blocks(client: TestClient) -> None:
    task_id, _, _ = create_task_with_snapshot(client)

    response = client.post(f"/api/tasks/{task_id}/review-results")

    assert response.status_code == 409
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["blocked_stage"] == "reviewing"
    assert "CONTRACT_PARSE_NOT_FOUND" in task["blocked_reason"]


def test_missing_rule_review_blocks(client: TestClient) -> None:
    task_id, _ = create_parsed_task(client)

    response = client.post(f"/api/tasks/{task_id}/review-results")

    assert response.status_code == 409
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["blocked_stage"] == "reviewing"
    assert "RULE_REVIEW_NOT_FOUND" in task["blocked_reason"]


def test_generation_failure_saves_failed_result_and_blocks(client: TestClient) -> None:
    class BrokenBuilder:
        version = "broken-1"

        def build(self, hits):
            raise RuntimeError("模拟结果生成异常")

    task_id, _, _ = create_reviewed_task(client)
    client.app.state.review_result_builder = BrokenBuilder()

    response = client.post(f"/api/tasks/{task_id}/review-results")

    assert response.status_code == 409
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["task_status"] == "blocked"
    assert task["blocked_stage"] == "reviewing"
    results = client.get(f"/api/tasks/{task_id}/review-results").json()
    assert results[0]["review_status"] == "failed"
    assert "REVIEW_RESULT_GENERATION_FAILED" in results[0]["review_error"]


def test_success_keeps_task_reviewing(client: TestClient) -> None:
    task_id, _, _ = create_reviewed_task(client)

    create_result(client, task_id)

    assert client.get(f"/api/tasks/{task_id}").json()["task_status"] == "reviewing"


def test_review_result_query_api_returns_history(client: TestClient) -> None:
    task_id, _, _ = create_reviewed_task(client)
    created = create_result(client, task_id)

    response = client.get(f"/api/tasks/{task_id}/review-results")

    assert response.status_code == 200
    assert response.json() == [created]


def test_review_result_creation_writes_task_log(client: TestClient) -> None:
    task_id, _, _ = create_reviewed_task(client)

    create_result(client, task_id)

    logs = client.get(f"/api/tasks/{task_id}/logs").json()
    assert any(log["log_type"] == "REVIEW_RESULT_CREATED" for log in logs)
