import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.core.task_state import InvalidTaskStateError
from app.models import ApprovalTask, TaskStatus
from app.services.task_state_service import TaskStateService
from tests.conftest import sync_all


def test_first_pull_creates_pending_task_and_log(client: TestClient) -> None:
    response = client.post("/api/tasks/sync", params={"limit": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 1
    assert body["updated_count"] == 0
    task = body["tasks"][0]
    assert task["task_status"] == "pending"

    logs = client.get(f"/api/tasks/{task['id']}/logs").json()
    assert any(log["log_type"] == "task_created" for log in logs)


def test_duplicate_pull_keeps_task_id_and_updates_fields(client: TestClient) -> None:
    response = client.post("/api/tasks/sync", params={"limit": 4})

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 3
    assert body["updated_count"] == 1
    assert len(body["tasks"]) == 3

    matches = [
        task for task in body["tasks"] if task["instance_id"] == "MOCK-INSTANCE-001"
    ]
    assert len(matches) == 1
    task = matches[0]
    assert task["id"] == 1
    assert task["approval_title"] == "采购合同审批（信息已更新）"

    logs = client.get(f"/api/tasks/{task['id']}/logs").json()
    assert [log["log_type"] for log in logs] == ["task_created", "duplicate_pull"]

    session_factory = client.app.state.session_factory
    with session_factory() as session:
        constraints = inspect(session.get_bind()).get_unique_constraints("approval_tasks")
    unique_columns = {tuple(item["column_names"]) for item in constraints}
    assert ("instance_id",) in unique_columns
    assert ("approval_code",) in unique_columns


def test_parsing_failure_moves_task_to_blocked(client: TestClient) -> None:
    tasks = sync_all(client)
    task = next(item for item in tasks if item["instance_id"] == "MOCK-INSTANCE-FAIL-001")

    started = client.post(f"/api/tasks/{task['id']}/start-parsing")
    blocked = client.post(f"/api/tasks/{task['id']}/simulate-parsing-failure")

    assert started.status_code == 200
    assert started.json()["task_status"] == "parsing"
    assert blocked.status_code == 200
    assert blocked.json()["task_status"] == "blocked"
    assert blocked.json()["blocked_reason"] == "模拟文档内容为空，无法继续解析"
    assert blocked.json()["retry_target"] == "parsing"

    logs = client.get(f"/api/tasks/{task['id']}/logs").json()
    assert any(log["log_type"] == "task_blocked" for log in logs)
    assert any("模拟文档内容为空" in log["log_content"] for log in logs)


def test_manual_retry_moves_blocked_task_to_parsing(client: TestClient) -> None:
    tasks = sync_all(client)
    task = next(item for item in tasks if item["instance_id"] == "MOCK-INSTANCE-FAIL-001")
    client.post(f"/api/tasks/{task['id']}/start-parsing")
    client.post(f"/api/tasks/{task['id']}/simulate-parsing-failure")

    response = client.post(f"/api/tasks/{task['id']}/retry")

    assert response.status_code == 200
    retried = response.json()
    assert retried["task_status"] == "parsing"
    assert retried["retry_count"] == 1
    assert retried["blocked_reason"] is None

    logs = client.get(f"/api/tasks/{task['id']}/logs").json()
    assert any(log["log_type"] == "manual_retry" for log in logs)
    assert any("blocked 变更为 parsing" in log["log_content"] for log in logs)


def test_illegal_state_transitions_are_rejected(client: TestClient) -> None:
    task = sync_all(client)[0]
    session_factory = client.app.state.session_factory

    with session_factory() as session:
        service = TaskStateService(session)
        with pytest.raises(InvalidTaskStateError, match="pending.*done"):
            service.transition_task(task["id"], TaskStatus.DONE)

    with session_factory() as session:
        stored_task = session.get(ApprovalTask, task["id"])
        assert stored_task is not None
        stored_task.task_status = TaskStatus.DONE
        session.commit()

    response = client.post(f"/api/tasks/{task['id']}/start-parsing")
    assert response.status_code == 409
    assert "done" in response.json()["detail"]
    assert "parsing" in response.json()["detail"]
