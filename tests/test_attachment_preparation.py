from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect


def sync_tasks(client: TestClient) -> list[dict]:
    response = client.post("/api/tasks/sync", params={"limit": 100})
    assert response.status_code == 200
    return response.json()["tasks"]


def get_task_by_instance(client: TestClient, instance_id: str) -> dict:
    return next(
        task for task in sync_tasks(client) if task["instance_id"] == instance_id
    )


def prepare(client: TestClient, task_id: int):
    return client.post(f"/api/tasks/{task_id}/prepare-attachment")


def test_normal_pdf_download_is_saved_to_file_and_database(client: TestClient) -> None:
    task = get_task_by_instance(client, "MOCK-INSTANCE-001")

    response = prepare(client, task["id"])

    assert response.status_code == 200
    body = response.json()
    attachment = body["attachment"]
    assert body["event"] == "ATTACHMENT_DOWNLOADED"
    assert body["task"]["task_status"] == "parsing"
    assert attachment["download_status"] == "success"
    assert attachment["original_file_name"] == "采购合同.pdf"
    assert attachment["file_type"] == "pdf"
    assert len(attachment["sha256"]) == 64
    assert Path(attachment["file_path"]).read_bytes() == b"mock pdf contract version 1"


def test_duplicate_download_does_not_create_duplicate_record(client: TestClient) -> None:
    task = get_task_by_instance(client, "MOCK-INSTANCE-001")
    first = prepare(client, task["id"]).json()["attachment"]
    second = prepare(client, task["id"]).json()["attachment"]

    attachments = client.get(f"/api/tasks/{task['id']}/attachments").json()

    assert len(attachments) == 1
    assert first["id"] == second["id"] == attachments[0]["id"]

    session_factory = client.app.state.session_factory
    with session_factory() as session:
        constraints = inspect(session.get_bind()).get_unique_constraints(
            "approval_attachments"
        )
    assert ("task_id", "external_attachment_id") in {
        tuple(item["column_names"]) for item in constraints
    }


def test_same_sha256_reuses_existing_local_file(client: TestClient) -> None:
    task = get_task_by_instance(client, "MOCK-INSTANCE-001")
    first = prepare(client, task["id"]).json()

    second = prepare(client, task["id"]).json()

    assert second["event"] == "ATTACHMENT_REUSED"
    assert second["attachment"]["file_path"] == first["attachment"]["file_path"]
    assert second["attachment"]["sha256"] == first["attachment"]["sha256"]


def test_missing_attachments_blocks_task(client: TestClient) -> None:
    task = get_task_by_instance(client, "MOCK-NO-ATTACHMENT")

    response = prepare(client, task["id"])

    assert response.status_code == 200
    assert response.json()["event"] == "ATTACHMENT_NOT_FOUND"
    assert response.json()["task"]["task_status"] == "blocked"
    assert response.json()["task"]["blocked_reason"].startswith("ATTACHMENT_NOT_FOUND")


def test_unrecognizable_main_contract_blocks_task(client: TestClient) -> None:
    task = get_task_by_instance(client, "MOCK-NO-MAIN-CONTRACT")

    response = prepare(client, task["id"])

    assert response.json()["event"] == "MAIN_CONTRACT_NOT_FOUND"
    assert response.json()["task"]["task_status"] == "blocked"
    assert response.json()["task"]["blocked_reason"].startswith(
        "MAIN_CONTRACT_NOT_FOUND"
    )


def test_unsupported_main_contract_type_blocks_task(client: TestClient) -> None:
    task = get_task_by_instance(client, "MOCK-UNSUPPORTED-TYPE")

    response = prepare(client, task["id"])

    assert response.json()["event"] == "UNSUPPORTED_ATTACHMENT_TYPE"
    assert response.json()["task"]["task_status"] == "blocked"
    attachments = client.get(f"/api/tasks/{task['id']}/attachments").json()
    assert len(attachments) == 1
    assert attachments[0]["file_type"] == "doc"
    assert attachments[0]["download_status"] == "failed"


def test_mock_download_failure_blocks_task(client: TestClient) -> None:
    task = get_task_by_instance(client, "MOCK-DOWNLOAD-FAIL")

    response = prepare(client, task["id"])

    assert response.json()["event"] == "ATTACHMENT_DOWNLOAD_FAILED"
    assert response.json()["task"]["task_status"] == "blocked"
    assert response.json()["task"]["blocked_reason"].startswith(
        "ATTACHMENT_DOWNLOAD_FAILED"
    )
    attachments = client.get(f"/api/tasks/{task['id']}/attachments").json()
    assert attachments[0]["download_status"] == "failed"
    logs = client.get(f"/api/tasks/{task['id']}/logs").json()
    assert any(log["log_type"] == "ATTACHMENT_DOWNLOAD_FAILED" for log in logs)
    assert any(
        "ATTACHMENT_DOWNLOAD_FAILED" in log["log_content"] for log in logs
    )


def test_blocked_retry_reexecutes_attachment_preparation(client: TestClient) -> None:
    task = get_task_by_instance(client, "MOCK-DOWNLOAD-FAIL")
    first = prepare(client, task["id"])
    assert first.json()["task"]["task_status"] == "blocked"

    retried = client.post(f"/api/tasks/{task['id']}/retry")

    assert retried.status_code == 200
    assert retried.json()["task_status"] == "parsing"
    assert retried.json()["retry_count"] == 1
    attachments = client.get(f"/api/tasks/{task['id']}/attachments").json()
    assert len(attachments) == 1
    assert attachments[0]["download_status"] == "success"
    assert Path(attachments[0]["file_path"]).is_file()


def test_unsafe_original_filename_is_sanitized_without_losing_name(client: TestClient) -> None:
    gateway = client.app.state.approval_gateway
    gateway.set_attachment_file_name(
        "MOCK-INSTANCE-001",
        "att_001",
        "../../采购合同:最终?.pdf",
    )
    task = get_task_by_instance(client, "MOCK-INSTANCE-001")

    attachment = prepare(client, task["id"]).json()["attachment"]
    saved_path = Path(attachment["file_path"])
    expected_root = client.app.state.contract_storage_root.resolve()

    assert attachment["original_file_name"] == "../../采购合同:最终?.pdf"
    assert saved_path.name == "att_001_采购合同_最终_.pdf"
    assert expected_root in saved_path.parents
    assert ".." not in saved_path.parts


def test_attachment_events_and_sha_change_are_logged(client: TestClient) -> None:
    task = get_task_by_instance(client, "MOCK-INSTANCE-001")
    first = prepare(client, task["id"]).json()["attachment"]
    prepare(client, task["id"])
    gateway = client.app.state.approval_gateway
    new_content = b"mock pdf contract version 2"
    gateway.set_attachment_content("MOCK-INSTANCE-001", "att_001", new_content)

    updated = prepare(client, task["id"]).json()
    logs = client.get(f"/api/tasks/{task['id']}/logs").json()
    attachment_events = [
        log for log in logs if log["log_type"].startswith("ATTACHMENT_")
    ]

    assert updated["event"] == "ATTACHMENT_UPDATED"
    assert updated["attachment"]["sha256"] == sha256(new_content).hexdigest()
    assert [log["log_type"] for log in attachment_events] == [
        "ATTACHMENT_DOWNLOADED",
        "ATTACHMENT_REUSED",
        "ATTACHMENT_UPDATED",
    ]
    update_log = attachment_events[-1]["log_content"]
    assert first["sha256"] in update_log
    assert updated["attachment"]["sha256"] in update_log


def test_english_keyword_selects_unmarked_docx_main_contract(client: TestClient) -> None:
    task = get_task_by_instance(client, "MOCK-INSTANCE-002")

    response = prepare(client, task["id"])

    assert response.status_code == 200
    assert response.json()["attachment"]["original_file_name"] == "Service Agreement.DOCX"
    assert response.json()["attachment"]["file_type"] == "docx"
