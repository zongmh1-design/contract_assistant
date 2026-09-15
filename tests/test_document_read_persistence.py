from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import (
    ApprovalAttachment,
    DocumentReadSnapshot,
    DownloadStatus,
)
from app.services.document_reading_service import DocumentReadingService


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def create_task_with_attachment(
    client: TestClient,
    path: Path,
    file_type: str,
) -> tuple[int, int]:
    task = client.post("/api/tasks/sync", params={"limit": 1}).json()["tasks"][0]
    assert client.post(f"/api/tasks/{task['id']}/start-parsing").status_code == 200

    content = path.read_bytes() if path.exists() else b"missing fixture placeholder"
    with client.app.state.session_factory() as session:
        attachment = ApprovalAttachment(
            task_id=task["id"],
            external_attachment_id=f"persist-{file_type}",
            original_file_name=path.name,
            file_type=file_type,
            file_path=str(path.resolve()),
            file_size=len(content),
            sha256=sha256(content).hexdigest(),
            is_main_contract=True,
            download_status=DownloadStatus.SUCCESS,
        )
        session.add(attachment)
        session.commit()
        return task["id"], attachment.id


def read_and_list(client: TestClient, task_id: int) -> tuple[dict, list[dict]]:
    response = client.post(f"/api/tasks/{task_id}/read-document")
    assert response.status_code == 200
    list_response = client.get(f"/api/tasks/{task_id}/document-reads")
    assert list_response.status_code == 200
    return response.json(), list_response.json()


def test_pdf_read_result_is_persisted(client: TestClient) -> None:
    task_id, attachment_id = create_task_with_attachment(
        client, FIXTURE_DIR / "text_contract.pdf", "pdf"
    )

    result, snapshots = read_and_list(client, task_id)

    assert result["read_status"] == "success"
    assert len(snapshots) == 1
    assert snapshots[0]["attachment_id"] == attachment_id
    assert snapshots[0]["read_method"] == "pdf_text"
    assert snapshots[0]["text"] == result["text"]


def test_docx_blocks_and_null_page_numbers_round_trip(client: TestClient) -> None:
    task_id, _ = create_task_with_attachment(
        client, FIXTURE_DIR / "normal_contract.docx", "docx"
    )

    result, snapshots = read_and_list(client, task_id)
    stored = snapshots[0]

    assert stored["read_method"] == "docx"
    assert stored["blocks"] == result["blocks"]
    assert "Payment term" in stored["text"]
    assert stored["page_count"] is None
    assert all(block["page_number"] is None for block in stored["blocks"])


def test_pdf_page_numbers_survive_json_storage(client: TestClient) -> None:
    task_id, _ = create_task_with_attachment(
        client, FIXTURE_DIR / "multi_page_contract.pdf", "pdf"
    )

    _, snapshots = read_and_list(client, task_id)

    assert snapshots[0]["page_count"] == 2
    assert [block["page_number"] for block in snapshots[0]["blocks"]] == [1, 2]


def test_blocks_are_stored_as_json_value(client: TestClient) -> None:
    task_id, _ = create_task_with_attachment(
        client, FIXTURE_DIR / "text_contract.pdf", "pdf"
    )
    client.post(f"/api/tasks/{task_id}/read-document")

    with client.app.state.session_factory() as session:
        snapshot = session.scalar(select(DocumentReadSnapshot))
        assert snapshot is not None
        assert isinstance(snapshot.blocks_json, list)
        assert snapshot.blocks_json[0]["page_number"] == 1


def test_ocr_required_result_is_persisted_and_blocks_task(client: TestClient) -> None:
    task_id, _ = create_task_with_attachment(
        client, FIXTURE_DIR / "contract_image.png", "png"
    )

    result, snapshots = read_and_list(client, task_id)

    assert result["read_status"] == "ocr_required"
    assert snapshots[0]["read_method"] == "image_pending_ocr"
    assert snapshots[0]["requires_ocr"] is True
    assert snapshots[0]["error_code"] == "OCR_REQUIRED"
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["task_status"] == "blocked"
    assert task["blocked_stage"] == "document_reading"


def test_reader_failure_is_persisted(client: TestClient) -> None:
    task_id, _ = create_task_with_attachment(
        client, FIXTURE_DIR / "corrupted.pdf", "pdf"
    )

    result, snapshots = read_and_list(client, task_id)

    assert result["error_code"] == "DOCUMENT_READ_FAILED"
    assert snapshots[0]["read_status"] == "failed"
    assert snapshots[0]["error_code"] == "DOCUMENT_READ_FAILED"


def test_missing_file_failure_is_persisted_consistently(
    client: TestClient, tmp_path: Path
) -> None:
    task_id, _ = create_task_with_attachment(
        client, tmp_path / "missing.pdf", "pdf"
    )

    _, snapshots = read_and_list(client, task_id)

    assert len(snapshots) == 1
    assert snapshots[0]["error_code"] == "DOCUMENT_FILE_NOT_FOUND"


def test_same_sha_and_reader_version_reuses_success(client: TestClient) -> None:
    task_id, _ = create_task_with_attachment(
        client, FIXTURE_DIR / "text_contract.pdf", "pdf"
    )

    first, _ = read_and_list(client, task_id)
    second, snapshots = read_and_list(client, task_id)
    logs = client.get(f"/api/tasks/{task_id}/logs").json()

    assert second == first
    assert len(snapshots) == 1
    assert any(log["log_type"] == "DOCUMENT_READ_REUSED" for log in logs)


def test_changed_attachment_sha_creates_new_snapshot(
    client: TestClient, tmp_path: Path
) -> None:
    document_path = tmp_path / "changing.pdf"
    document_path.write_bytes((FIXTURE_DIR / "text_contract.pdf").read_bytes())
    task_id, attachment_id = create_task_with_attachment(client, document_path, "pdf")
    client.post(f"/api/tasks/{task_id}/read-document")

    new_content = (FIXTURE_DIR / "multi_page_contract.pdf").read_bytes()
    document_path.write_bytes(new_content)
    new_sha256 = sha256(new_content).hexdigest()
    with client.app.state.session_factory() as session:
        attachment = session.get(ApprovalAttachment, attachment_id)
        assert attachment is not None
        attachment.sha256 = new_sha256
        attachment.file_size = len(new_content)
        session.commit()

    _, snapshots = read_and_list(client, task_id)

    assert len(snapshots) == 2
    assert snapshots[0]["file_sha256"] != snapshots[1]["file_sha256"]
    assert snapshots[1]["file_sha256"] == new_sha256
    assert snapshots[1]["page_count"] == 2


def test_changed_reader_version_creates_new_snapshot(client: TestClient) -> None:
    task_id, _ = create_task_with_attachment(
        client, FIXTURE_DIR / "text_contract.pdf", "pdf"
    )
    client.post(f"/api/tasks/{task_id}/read-document")

    with client.app.state.session_factory() as session:
        result = DocumentReadingService(
            session, reader_version="2.0-test"
        ).read_main_document(task_id)
        assert result.read_status.value == "success"

    snapshots = client.get(f"/api/tasks/{task_id}/document-reads").json()
    assert [item["reader_version"] for item in snapshots] == ["1.0", "2.0-test"]


def test_document_read_query_returns_404_for_unknown_task(client: TestClient) -> None:
    response = client.get("/api/tasks/999999/document-reads")

    assert response.status_code == 404


def test_reading_does_not_duplicate_task_or_attachment(client: TestClient) -> None:
    task_id, _ = create_task_with_attachment(
        client, FIXTURE_DIR / "text_contract.pdf", "pdf"
    )

    client.post(f"/api/tasks/{task_id}/read-document")
    client.post(f"/api/tasks/{task_id}/read-document")

    with client.app.state.session_factory() as session:
        attachment_count = session.scalar(select(func.count(ApprovalAttachment.id)))
        snapshot_count = session.scalar(select(func.count(DocumentReadSnapshot.id)))
    assert len(client.get("/api/tasks").json()) == 1
    assert attachment_count == 1
    assert snapshot_count == 1
