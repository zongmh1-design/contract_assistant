from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.models import ApprovalAttachment, ApprovalTask, DownloadStatus
from app.parsers import (
    DocxDocumentReader,
    DocumentReaderRouter,
    ImageDocumentReader,
    PdfDocumentReader,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def attach_fixture(
    client: TestClient,
    path: Path,
    file_type: str,
) -> tuple[dict, dict]:
    sync_response = client.post("/api/tasks/sync", params={"limit": 1})
    task = sync_response.json()["tasks"][0]
    start_response = client.post(f"/api/tasks/{task['id']}/start-parsing")
    assert start_response.status_code == 200

    content = path.read_bytes() if path.exists() else b"missing fixture placeholder"
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        attachment = ApprovalAttachment(
            task_id=task["id"],
            external_attachment_id=f"fixture-{file_type}",
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
        attachment_id = attachment.id

    return task, {"id": attachment_id}


def read_fixture(client: TestClient, path: Path, file_type: str) -> tuple[dict, dict]:
    task, attachment = attach_fixture(client, path, file_type)
    response = client.post(f"/api/tasks/{task['id']}/read-document")
    assert response.status_code == 200
    return task, response.json() | {"expected_document_id": attachment["id"]}


def test_pdf_extracts_text(client: TestClient) -> None:
    _, result = read_fixture(client, FIXTURE_DIR / "text_contract.pdf", "pdf")

    assert result["read_status"] == "success"
    assert "enough readable text" in result["text"]
    assert result["requires_ocr"] is False
    assert result["document_id"] == result["expected_document_id"]


def test_pdf_blocks_keep_one_based_page_numbers(client: TestClient) -> None:
    _, result = read_fixture(client, FIXTURE_DIR / "multi_page_contract.pdf", "pdf")

    assert result["page_count"] == 2
    assert [block["page_number"] for block in result["blocks"]] == [1, 2]
    assert [block["block_index"] for block in result["blocks"]] == [0, 1]


def test_multi_page_pdf_text_order_is_preserved(client: TestClient) -> None:
    _, result = read_fixture(client, FIXTURE_DIR / "multi_page_contract.pdf", "pdf")

    first_position = result["text"].index("First page")
    second_position = result["text"].index("Second page")
    assert first_position < second_position


def test_pdf_without_extractable_text_requires_ocr_and_blocks(client: TestClient) -> None:
    task, result = read_fixture(client, FIXTURE_DIR / "empty_text.pdf", "pdf")

    assert result["read_status"] == "ocr_required"
    assert result["requires_ocr"] is True
    assert result["error_code"] == "OCR_REQUIRED"
    stored_task = client.get(f"/api/tasks/{task['id']}").json()
    assert stored_task["task_status"] == "blocked"
    assert stored_task["blocked_stage"] == "document_reading"


def test_docx_extracts_paragraphs_without_fake_page_numbers(client: TestClient) -> None:
    _, result = read_fixture(client, FIXTURE_DIR / "normal_contract.docx", "docx")

    assert result["read_status"] == "success"
    assert "DOCX body text" in result["text"]
    assert result["page_count"] is None
    assert all(block["page_number"] is None for block in result["blocks"])


def test_docx_table_and_surrounding_order_are_preserved(client: TestClient) -> None:
    _, result = read_fixture(client, FIXTURE_DIR / "normal_contract.docx", "docx")
    texts = [block["text"] for block in result["blocks"]]

    body_index = next(index for index, text in enumerate(texts) if "body text" in text)
    table_index = next(index for index, text in enumerate(texts) if "Payment term" in text)
    after_index = next(index for index, text in enumerate(texts) if "after the table" in text)
    assert body_index < table_index < after_index
    assert "Thirty days" in texts[table_index]


@pytest.mark.parametrize(
    ("file_name", "file_type"),
    [("contract_image.jpg", "jpg"), ("contract_image.png", "png")],
)
def test_images_require_ocr(
    client: TestClient, file_name: str, file_type: str
) -> None:
    task, result = read_fixture(client, FIXTURE_DIR / file_name, file_type)

    assert result["read_status"] == "ocr_required"
    assert result["requires_ocr"] is True
    assert result["error_code"] == "OCR_REQUIRED"
    assert result["blocks"] == []
    assert client.get(f"/api/tasks/{task['id']}").json()["task_status"] == "blocked"


def test_missing_file_returns_explicit_failure(client: TestClient, tmp_path: Path) -> None:
    missing_path = tmp_path / "does-not-exist.pdf"
    task, result = read_fixture(client, missing_path, "pdf")

    assert result["read_status"] == "failed"
    assert result["error_code"] == "DOCUMENT_FILE_NOT_FOUND"
    assert client.get(f"/api/tasks/{task['id']}").json()["task_status"] == "blocked"


@pytest.mark.parametrize(
    ("file_name", "file_type"),
    [("corrupted.pdf", "pdf"), ("corrupted.docx", "docx")],
)
def test_corrupted_document_returns_read_failed(
    client: TestClient, file_name: str, file_type: str
) -> None:
    _, result = read_fixture(client, FIXTURE_DIR / file_name, file_type)

    assert result["read_status"] == "failed"
    assert result["error_code"] == "DOCUMENT_READ_FAILED"
    assert result["text"] == ""


def test_zero_byte_file_cannot_report_success(client: TestClient, tmp_path: Path) -> None:
    empty_file = tmp_path / "zero-byte.pdf"
    empty_file.write_bytes(b"")

    _, result = read_fixture(client, empty_file, "pdf")

    assert result["read_status"] == "failed"
    assert result["error_code"] == "DOCUMENT_FILE_EMPTY"


def test_empty_docx_content_cannot_report_success(client: TestClient) -> None:
    _, result = read_fixture(client, FIXTURE_DIR / "empty_contract.docx", "docx")

    assert result["read_status"] == "failed"
    assert result["error_code"] == "DOCUMENT_CONTENT_EMPTY"


def test_reader_router_selects_expected_reader() -> None:
    router = DocumentReaderRouter()

    assert isinstance(router.reader_for("PDF"), PdfDocumentReader)
    assert isinstance(router.reader_for(".docx"), DocxDocumentReader)
    assert isinstance(router.reader_for("jpg"), ImageDocumentReader)
    assert isinstance(router.reader_for("jpeg"), ImageDocumentReader)
    assert isinstance(router.reader_for("png"), ImageDocumentReader)
    assert router.reader_for("doc") is None


def test_read_failure_writes_task_log_with_error_code(client: TestClient) -> None:
    task, _ = read_fixture(client, FIXTURE_DIR / "corrupted.pdf", "pdf")

    logs = client.get(f"/api/tasks/{task['id']}/logs").json()

    assert any(log["log_type"] == "DOCUMENT_READ_FAILED" for log in logs)
    assert any("PDF 读取失败" in log["log_content"] for log in logs)
    assert any(log["log_type"] == "task_blocked" for log in logs)


def test_reading_does_not_duplicate_task_or_attachment(client: TestClient) -> None:
    task, _ = attach_fixture(client, FIXTURE_DIR / "text_contract.pdf", "pdf")

    first = client.post(f"/api/tasks/{task['id']}/read-document")
    second = client.post(f"/api/tasks/{task['id']}/read-document")

    assert first.status_code == second.status_code == 200
    assert len(client.get("/api/tasks").json()) == 1
    assert len(client.get(f"/api/tasks/{task['id']}/attachments").json()) == 1
