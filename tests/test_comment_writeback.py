from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.integrations.ocr import MockOcrEngine
from app.models import (
    ApprovalAttachment,
    ApprovalTask,
    CommentLog,
    ContractParse,
    DocumentReadSnapshot,
    ReviewResult,
    RuleHit,
    TaskStatus,
)
from tests.test_review_result import create_result, create_reviewed_task


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def create_ready_task(client: TestClient) -> tuple[int, int]:
    task_id, _, _ = create_reviewed_task(client)
    review_result = create_result(client, task_id)
    return task_id, review_result["id"]


def write_comment(client: TestClient, task_id: int):
    return client.post(f"/api/tasks/{task_id}/write-comment")


def test_successful_review_result_is_written_back(client: TestClient) -> None:
    task_id, review_result_id = create_ready_task(client)

    response = write_comment(client, task_id)

    assert response.status_code == 200
    body = response.json()
    assert body["reused"] is False
    assert body["comment_log"]["review_result_id"] == review_result_id
    assert body["comment_log"]["write_status"] == "success"


def test_success_updates_write_status_and_task_status(client: TestClient) -> None:
    task_id, _ = create_ready_task(client)

    result = write_comment(client, task_id).json()

    assert result["task"]["write_status"] == "success"
    assert result["task"]["task_status"] == "done"
    stored = client.get(f"/api/tasks/{task_id}").json()
    assert stored["write_status"] == "success"
    assert stored["task_status"] == "done"


def test_success_comment_log_saves_external_comment_id(client: TestClient) -> None:
    task_id, review_result_id = create_ready_task(client)

    log = write_comment(client, task_id).json()["comment_log"]

    assert log["external_comment_id"] == f"mock-comment-{review_result_id}"
    assert log["write_response_text"] == "Mock 审批评论写入成功"
    assert log["error_code"] is None


def test_gateway_failure_blocks_and_keeps_failed_attempt(client: TestClient) -> None:
    task_id, review_result_id = create_ready_task(client)
    gateway = client.app.state.approval_gateway
    gateway.set_comment_write_mode("MOCK-INSTANCE-001", "failure")

    response = write_comment(client, task_id)

    assert response.status_code == 409
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["task_status"] == "blocked"
    assert task["write_status"] == "failed"
    assert task["blocked_stage"] == "comment_writeback"
    assert "COMMENT_WRITE_FAILED" in task["blocked_reason"]
    logs = client.get(f"/api/tasks/{task_id}/comment-logs").json()
    assert len(logs) == 1
    assert logs[0]["review_result_id"] == review_result_id
    assert logs[0]["write_status"] == "failed"
    assert logs[0]["error_code"] == "COMMENT_WRITE_FAILED"


def test_invalid_gateway_response_is_recorded(client: TestClient) -> None:
    task_id, _ = create_ready_task(client)
    gateway = client.app.state.approval_gateway
    gateway.set_comment_write_mode("MOCK-INSTANCE-001", "invalid")

    response = write_comment(client, task_id)

    assert response.status_code == 409
    log = client.get(f"/api/tasks/{task_id}/comment-logs").json()[0]
    assert log["write_status"] == "failed"
    assert log["error_code"] == "COMMENT_RESPONSE_INVALID"
    assert "external_comment_id" in log["write_response_text"]


def test_comment_retry_only_reexecutes_writeback(client: TestClient) -> None:
    task_id, _ = create_ready_task(client)
    gateway = client.app.state.approval_gateway
    ocr_engine = MockOcrEngine([["不应执行 OCR"]])
    client.app.state.ocr_engine = ocr_engine
    gateway.set_comment_write_mode("MOCK-INSTANCE-001", "failure")
    write_comment(client, task_id)
    before_download_calls = gateway.attachment_download_call_count
    with client.app.state.session_factory() as session:
        before_counts = (
            session.scalar(select(func.count()).select_from(ApprovalAttachment)),
            session.scalar(select(func.count()).select_from(DocumentReadSnapshot)),
            session.scalar(select(func.count()).select_from(ContractParse)),
            session.scalar(select(func.count()).select_from(RuleHit)),
            session.scalar(select(func.count()).select_from(ReviewResult)),
        )
    gateway.set_comment_write_mode("MOCK-INSTANCE-001", "success")

    retried = client.post(f"/api/tasks/{task_id}/retry")

    assert retried.status_code == 200
    assert retried.json()["task_status"] == "done"
    assert gateway.attachment_download_call_count == before_download_calls
    assert ocr_engine.calls == []
    with client.app.state.session_factory() as session:
        after_counts = (
            session.scalar(select(func.count()).select_from(ApprovalAttachment)),
            session.scalar(select(func.count()).select_from(DocumentReadSnapshot)),
            session.scalar(select(func.count()).select_from(ContractParse)),
            session.scalar(select(func.count()).select_from(RuleHit)),
            session.scalar(select(func.count()).select_from(ReviewResult)),
        )
    assert after_counts == before_counts
    assert [
        item["write_status"]
        for item in client.get(f"/api/tasks/{task_id}/comment-logs").json()
    ] == ["failed", "success"]


def test_comment_retry_logs_dedicated_resume_action(client: TestClient) -> None:
    task_id, _ = create_ready_task(client)
    gateway = client.app.state.approval_gateway
    gateway.set_comment_write_mode("MOCK-INSTANCE-001", "failure")
    write_comment(client, task_id)
    gateway.set_comment_write_mode("MOCK-INSTANCE-001", "success")

    client.post(f"/api/tasks/{task_id}/retry")

    logs = client.get(f"/api/tasks/{task_id}/logs").json()
    assert any(log["log_type"] == "COMMENT_WRITE_RETRY_RESUMED" for log in logs)
    assert any("仅恢复评论回写" in log["log_content"] for log in logs)


def test_successful_repeat_reuses_without_gateway_call(client: TestClient) -> None:
    task_id, _ = create_ready_task(client)
    gateway = client.app.state.approval_gateway
    first = write_comment(client, task_id).json()
    calls_after_first = gateway.comment_write_call_count

    second_response = write_comment(client, task_id)

    assert second_response.status_code == 200
    second = second_response.json()
    assert second["reused"] is True
    assert second["comment_log"]["id"] == first["comment_log"]["id"]
    assert gateway.comment_write_call_count == calls_after_first
    assert gateway.created_comment_count == 1
    assert len(client.get(f"/api/tasks/{task_id}/comment-logs").json()) == 1


def test_failed_attempt_can_be_retried_with_new_comment_log(client: TestClient) -> None:
    task_id, _ = create_ready_task(client)
    gateway = client.app.state.approval_gateway
    gateway.set_comment_write_mode("MOCK-INSTANCE-001", "failure")
    write_comment(client, task_id)
    gateway.set_comment_write_mode("MOCK-INSTANCE-001", "success")

    response = client.post(f"/api/tasks/{task_id}/retry")

    assert response.status_code == 200
    logs = client.get(f"/api/tasks/{task_id}/comment-logs").json()
    assert len(logs) == 2
    assert logs[0]["id"] != logs[1]["id"]
    assert [item["write_status"] for item in logs] == ["failed", "success"]


def test_missing_review_result_is_rejected_and_blocked(client: TestClient) -> None:
    task_id, _, _ = create_reviewed_task(client)

    response = write_comment(client, task_id)

    assert response.status_code == 409
    assert "REVIEW_RESULT_NOT_FOUND" in response.json()["detail"]
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["task_status"] == "blocked"
    assert task["blocked_stage"] == "comment_writeback"
    assert client.get(f"/api/tasks/{task_id}/comment-logs").json() == []


def test_empty_comment_text_is_rejected_without_gateway_call(client: TestClient) -> None:
    task_id, review_result_id = create_ready_task(client)
    with client.app.state.session_factory() as session:
        result = session.get(ReviewResult, review_result_id)
        assert result is not None
        result.comment_text = "   "
        session.commit()
    gateway = client.app.state.approval_gateway

    response = write_comment(client, task_id)

    assert response.status_code == 409
    assert "COMMENT_TEXT_EMPTY" in response.json()["detail"]
    assert gateway.comment_write_call_count == 0
    log = client.get(f"/api/tasks/{task_id}/comment-logs").json()[0]
    assert log["error_code"] == "COMMENT_TEXT_EMPTY"


def test_non_reviewing_task_cannot_start_new_writeback(client: TestClient) -> None:
    task_id, _ = create_ready_task(client)
    with client.app.state.session_factory() as session:
        task = session.get(ApprovalTask, task_id)
        assert task is not None
        task.task_status = TaskStatus.PARSING
        session.commit()

    response = write_comment(client, task_id)

    assert response.status_code == 409
    assert "parsing" in response.json()["detail"]
    assert client.app.state.approval_gateway.comment_write_call_count == 0


def test_comment_task_logs_cover_full_write_lifecycle(client: TestClient) -> None:
    task_id, _ = create_ready_task(client)

    write_comment(client, task_id)

    log_types = {
        item["log_type"] for item in client.get(f"/api/tasks/{task_id}/logs").json()
    }
    assert {
        "COMMENT_WRITE_STARTED",
        "COMMENT_WRITE_SUCCEEDED",
        "TASK_COMPLETED",
    }.issubset(log_types)


def test_comment_log_query_api_returns_attempts(client: TestClient) -> None:
    task_id, _ = create_ready_task(client)
    created = write_comment(client, task_id).json()["comment_log"]

    response = client.get(f"/api/tasks/{task_id}/comment-logs")

    assert response.status_code == 200
    assert response.json() == [created]


def test_mock_gateway_is_idempotent_for_same_review_id(client: TestClient) -> None:
    gateway = client.app.state.approval_gateway

    first = gateway.write_approval_comment("MOCK-INSTANCE-001", 88, "审查意见")
    second = gateway.write_approval_comment("MOCK-INSTANCE-001", 88, "审查意见")

    assert second == first
    assert gateway.created_comment_count == 1
    assert gateway.comment_write_call_count == 2


def test_non_comment_blocked_stage_is_not_sent_to_attachment_retry(
    client: TestClient,
) -> None:
    task_id, _ = create_ready_task(client)
    with client.app.state.session_factory() as session:
        task = session.get(ApprovalTask, task_id)
        assert task is not None
        task.task_status = TaskStatus.BLOCKED
        task.blocked_stage = "field_extraction"
        task.blocked_reason = "模拟字段提取失败"
        task.retry_target = "parsing"
        session.commit()
    before_downloads = client.app.state.approval_gateway.attachment_download_call_count

    response = client.post(f"/api/tasks/{task_id}/retry")

    assert response.status_code == 409
    assert "field_extraction" in response.json()["detail"]
    assert client.app.state.approval_gateway.attachment_download_call_count == before_downloads


def test_complete_mock_business_flow_reaches_done(client: TestClient) -> None:
    gateway = client.app.state.approval_gateway
    gateway.set_attachment_content(
        "MOCK-INSTANCE-001",
        "att_001",
        (FIXTURE_DIR / "text_contract.pdf").read_bytes(),
    )
    task = client.post("/api/tasks/sync", params={"limit": 1}).json()["tasks"][0]

    attachment = client.post(f"/api/tasks/{task['id']}/prepare-attachment")
    document = client.post(f"/api/tasks/{task['id']}/read-document")
    contract_parse = client.post(f"/api/tasks/{task['id']}/parse-contract")
    rule_review = client.post(f"/api/tasks/{task['id']}/run-rules")
    review_result = client.post(f"/api/tasks/{task['id']}/review-results")
    writeback = client.post(f"/api/tasks/{task['id']}/write-comment")

    assert attachment.status_code == 200
    assert document.status_code == 200
    assert contract_parse.status_code == 200
    assert rule_review.status_code == 200
    assert review_result.status_code == 200
    assert writeback.status_code == 200
    assert writeback.json()["task"]["task_status"] == "done"
    assert writeback.json()["task"]["write_status"] == "success"
    with client.app.state.session_factory() as session:
        comment_log = session.scalar(select(CommentLog))
        assert comment_log is not None
        assert comment_log.task_id == task["id"]
        assert comment_log.review_result.contract_parse.document_read_snapshot.attachment.task_id == task["id"]
        assert comment_log.review_result.rule_hits


def test_full_flow_persists_every_domain_stage(client: TestClient) -> None:
    gateway = client.app.state.approval_gateway
    gateway.set_attachment_content(
        "MOCK-INSTANCE-001",
        "att_001",
        (FIXTURE_DIR / "text_contract.pdf").read_bytes(),
    )
    task = client.post("/api/tasks/sync", params={"limit": 1}).json()["tasks"][0]
    client.post(f"/api/tasks/{task['id']}/prepare-attachment")
    client.post(f"/api/tasks/{task['id']}/read-document")
    client.post(f"/api/tasks/{task['id']}/parse-contract")
    client.post(f"/api/tasks/{task['id']}/run-rules")
    client.post(f"/api/tasks/{task['id']}/review-results")
    client.post(f"/api/tasks/{task['id']}/write-comment")

    with client.app.state.session_factory() as session:
        models = (
            ApprovalTask,
            ApprovalAttachment,
            DocumentReadSnapshot,
            ContractParse,
            RuleHit,
            ReviewResult,
            CommentLog,
        )
        assert all(
            session.scalar(select(func.count()).select_from(model)) >= 1
            for model in models
        )
