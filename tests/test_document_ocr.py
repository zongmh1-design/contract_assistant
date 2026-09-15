from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.integrations.ocr import (
    MockOcrEngine,
    PdfRenderError,
    RapidOcrEngine,
)
from app.models import (
    ApprovalAttachment,
    ApprovalTask,
    DocumentReadSnapshot,
    DocumentReadStatus,
    DownloadStatus,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"


class FailingPdfRenderer:
    def render_pages(self, pdf_path: Path, output_directory: Path) -> list:
        raise PdfRenderError("模拟 PDF 渲染失败")


def create_ocr_required_task(
    client: TestClient,
    path: Path,
    file_type: str,
) -> tuple[int, int]:
    task = client.post("/api/tasks/sync", params={"limit": 1}).json()["tasks"][0]
    assert client.post(f"/api/tasks/{task['id']}/start-parsing").status_code == 200

    content = path.read_bytes() if path.exists() else b"missing OCR input"
    with client.app.state.session_factory() as session:
        attachment = ApprovalAttachment(
            task_id=task["id"],
            external_attachment_id=f"ocr-{file_type}",
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

    read_response = client.post(f"/api/tasks/{task['id']}/read-document")
    assert read_response.status_code == 200
    assert read_response.json()["read_status"] == "ocr_required"
    return task["id"], attachment_id


def list_snapshots(client: TestClient, task_id: int) -> list[dict]:
    response = client.get(f"/api/tasks/{task_id}/document-reads")
    assert response.status_code == 200
    return response.json()


@pytest.mark.parametrize(
    ("file_name", "file_type"),
    [("contract_image.jpg", "jpg"), ("contract_image.png", "png")],
)
def test_image_ocr_succeeds_and_preserves_position(
    client: TestClient, file_name: str, file_type: str
) -> None:
    task_id, _ = create_ocr_required_task(
        client, FIXTURE_DIR / file_name, file_type
    )
    engine = MockOcrEngine([["合同扫描文本 first", "Agreement second line"]])
    client.app.state.ocr_engine = engine

    response = client.post(f"/api/tasks/{task_id}/ocr")

    assert response.status_code == 200
    result = response.json()
    assert result["read_status"] == "success"
    assert result["requires_ocr"] is False
    assert result["page_count"] == 1
    assert [block["page_number"] for block in result["blocks"]] == [1, 1]
    assert [block["block_index"] for block in result["blocks"]] == [0, 1]
    assert client.get(f"/api/tasks/{task_id}").json()["task_status"] == "parsing"


def test_scanned_pdf_ocr_keeps_page_and_global_block_order(
    client: TestClient,
) -> None:
    task_id, _ = create_ocr_required_task(
        client, FIXTURE_DIR / "multi_page_scanned_contract.pdf", "pdf"
    )
    engine = MockOcrEngine(
        [["First scanned page contract"], ["Second scanned page agreement"]]
    )
    client.app.state.ocr_engine = engine

    result = client.post(f"/api/tasks/{task_id}/ocr").json()

    assert result["page_count"] == 2
    assert [block["page_number"] for block in result["blocks"]] == [1, 2]
    assert [block["block_index"] for block in result["blocks"]] == [0, 1]
    assert result["text"].index("First") < result["text"].index("Second")
    assert all(not path.exists() for path in engine.calls)


def test_ocr_success_adds_snapshot_and_keeps_required_snapshot(
    client: TestClient,
) -> None:
    task_id, attachment_id = create_ocr_required_task(
        client, FIXTURE_DIR / "contract_image.png", "png"
    )
    client.app.state.ocr_engine = MockOcrEngine([["Successful OCR contract text"]])

    client.post(f"/api/tasks/{task_id}/ocr")
    snapshots = list_snapshots(client, task_id)

    assert len(snapshots) == 2
    assert snapshots[0]["attachment_id"] == attachment_id
    assert snapshots[0]["read_status"] == "ocr_required"
    assert snapshots[1]["read_method"] == "ocr"
    assert snapshots[1]["read_status"] == "success"
    assert snapshots[1]["requires_ocr"] is False
    assert snapshots[1]["reader_version"] == "mock-ocr-1"


def test_empty_ocr_result_is_saved_and_blocks(client: TestClient) -> None:
    task_id, _ = create_ocr_required_task(
        client, FIXTURE_DIR / "contract_image.png", "png"
    )
    client.app.state.ocr_engine = MockOcrEngine([[]])

    result = client.post(f"/api/tasks/{task_id}/ocr").json()

    assert result["error_code"] == "OCR_CONTENT_EMPTY"
    assert list_snapshots(client, task_id)[-1]["error_code"] == "OCR_CONTENT_EMPTY"
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["task_status"] == "blocked"
    assert task["blocked_stage"] == "document_reading"


def test_ocr_engine_exception_is_saved_and_blocks(client: TestClient) -> None:
    task_id, _ = create_ocr_required_task(
        client, FIXTURE_DIR / "contract_image.jpg", "jpg"
    )
    client.app.state.ocr_engine = MockOcrEngine(
        failure_message="模拟 OCR Provider 异常"
    )

    result = client.post(f"/api/tasks/{task_id}/ocr").json()

    assert result["error_code"] == "OCR_ENGINE_FAILED"
    assert list_snapshots(client, task_id)[-1]["read_status"] == "failed"
    assert client.get(f"/api/tasks/{task_id}").json()["task_status"] == "blocked"


def test_pdf_render_failure_is_saved_and_blocks(client: TestClient) -> None:
    task_id, _ = create_ocr_required_task(
        client, FIXTURE_DIR / "multi_page_scanned_contract.pdf", "pdf"
    )
    client.app.state.ocr_engine = MockOcrEngine()
    client.app.state.pdf_page_renderer = FailingPdfRenderer()

    result = client.post(f"/api/tasks/{task_id}/ocr").json()

    assert result["error_code"] == "PDF_RENDER_FAILED"
    assert list_snapshots(client, task_id)[-1]["error_code"] == "PDF_RENDER_FAILED"
    assert client.get(f"/api/tasks/{task_id}").json()["task_status"] == "blocked"


def test_successful_ocr_is_reused_for_same_sha_and_version(client: TestClient) -> None:
    task_id, _ = create_ocr_required_task(
        client, FIXTURE_DIR / "contract_image.png", "png"
    )
    engine = MockOcrEngine([["Reusable OCR contract text"]])
    client.app.state.ocr_engine = engine

    first = client.post(f"/api/tasks/{task_id}/ocr").json()
    second = client.post(f"/api/tasks/{task_id}/ocr").json()

    assert second == first
    assert len(engine.calls) == 1
    assert len(list_snapshots(client, task_id)) == 2
    logs = client.get(f"/api/tasks/{task_id}/logs").json()
    assert any(log["log_type"] == "DOCUMENT_OCR_REUSED" for log in logs)


def test_changed_sha_runs_ocr_again(client: TestClient, tmp_path: Path) -> None:
    image_path = tmp_path / "changing.png"
    image_path.write_bytes((FIXTURE_DIR / "contract_image.png").read_bytes())
    task_id, attachment_id = create_ocr_required_task(client, image_path, "png")
    engine = MockOcrEngine([["First OCR contract version"]])
    client.app.state.ocr_engine = engine
    client.post(f"/api/tasks/{task_id}/ocr")

    changed_content = image_path.read_bytes() + b"changed-upstream-content"
    image_path.write_bytes(changed_content)
    with client.app.state.session_factory() as session:
        attachment = session.get(ApprovalAttachment, attachment_id)
        assert attachment is not None
        attachment.sha256 = sha256(changed_content).hexdigest()
        attachment.file_size = len(changed_content)
        session.commit()
    client.post(f"/api/tasks/{task_id}/read-document")
    client.post(f"/api/tasks/{task_id}/ocr")

    snapshots = list_snapshots(client, task_id)
    assert len(engine.calls) == 2
    assert len([item for item in snapshots if item["read_method"] == "ocr"]) == 2
    assert snapshots[-1]["file_sha256"] == sha256(changed_content).hexdigest()


def test_changed_ocr_provider_version_runs_again(client: TestClient) -> None:
    task_id, _ = create_ocr_required_task(
        client, FIXTURE_DIR / "contract_image.png", "png"
    )
    client.app.state.ocr_engine = MockOcrEngine(
        [["First provider OCR contract"]], version="mock-ocr-1"
    )
    client.post(f"/api/tasks/{task_id}/ocr")

    second_engine = MockOcrEngine(
        [["Second provider OCR contract"]], version="mock-ocr-2"
    )
    client.app.state.ocr_engine = second_engine
    client.post(f"/api/tasks/{task_id}/ocr")

    ocr_snapshots = [
        item
        for item in list_snapshots(client, task_id)
        if item["read_method"] == "ocr"
    ]
    assert [item["reader_version"] for item in ocr_snapshots] == [
        "mock-ocr-1",
        "mock-ocr-2",
    ]
    assert len(second_engine.calls) == 1


def test_text_pdf_rejects_unnecessary_ocr(client: TestClient) -> None:
    task = client.post("/api/tasks/sync", params={"limit": 1}).json()["tasks"][0]
    client.post(f"/api/tasks/{task['id']}/start-parsing")
    path = FIXTURE_DIR / "text_contract.pdf"
    content = path.read_bytes()
    with client.app.state.session_factory() as session:
        session.add(
            ApprovalAttachment(
                task_id=task["id"],
                external_attachment_id="text-pdf",
                original_file_name=path.name,
                file_type="pdf",
                file_path=str(path.resolve()),
                file_size=len(content),
                sha256=sha256(content).hexdigest(),
                is_main_contract=True,
                download_status=DownloadStatus.SUCCESS,
            )
        )
        session.commit()
    assert client.post(f"/api/tasks/{task['id']}/read-document").json()[
        "read_status"
    ] == "success"

    response = client.post(f"/api/tasks/{task['id']}/ocr")

    assert response.status_code == 409
    assert "OCR_NOT_REQUIRED" in response.json()["detail"]


def test_blocked_ocr_resume_does_not_download_or_duplicate(client: TestClient) -> None:
    task_id, _ = create_ocr_required_task(
        client, FIXTURE_DIR / "contract_image.jpg", "jpg"
    )
    client.app.state.ocr_engine = MockOcrEngine([["Recovered OCR contract text"]])
    gateway = client.app.state.approval_gateway
    gateway.download_contract_attachment = Mock(
        wraps=gateway.download_contract_attachment
    )

    response = client.post(f"/api/tasks/{task_id}/ocr")

    assert response.status_code == 200
    gateway.download_contract_attachment.assert_not_called()
    logs = client.get(f"/api/tasks/{task_id}/logs").json()
    log_types = [log["log_type"] for log in logs]
    assert "DOCUMENT_OCR_RESUMED" in log_types
    assert "DOCUMENT_OCR_STARTED" in log_types
    assert "DOCUMENT_OCR_SUCCESS" in log_types
    with client.app.state.session_factory() as session:
        attachment_count = session.scalar(select(func.count(ApprovalAttachment.id)))
        task_count = session.scalar(select(func.count(ApprovalTask.id)))
        snapshot_count = session.scalar(select(func.count(DocumentReadSnapshot.id)))
    assert attachment_count == 1
    assert task_count == 1
    assert snapshot_count == 2


def test_real_rapidocr_smoke_on_fixture() -> None:
    result = RapidOcrEngine().recognize_image(FIXTURE_DIR / "contract_image.png")

    assert any("contract" in line.text.lower() for line in result.lines)


def test_unsupported_ocr_file_type_is_saved_and_blocks(client: TestClient) -> None:
    task = client.post("/api/tasks/sync", params={"limit": 1}).json()["tasks"][0]
    client.post(f"/api/tasks/{task['id']}/start-parsing")
    path = FIXTURE_DIR / "normal_contract.docx"
    content = path.read_bytes()
    with client.app.state.session_factory() as session:
        attachment = ApprovalAttachment(
            task_id=task["id"],
            external_attachment_id="unsupported-ocr-docx",
            original_file_name=path.name,
            file_type="docx",
            file_path=str(path.resolve()),
            file_size=len(content),
            sha256=sha256(content).hexdigest(),
            is_main_contract=True,
            download_status=DownloadStatus.SUCCESS,
        )
        session.add(attachment)
        session.flush()
        session.add(
            DocumentReadSnapshot(
                attachment_id=attachment.id,
                read_method="docx",
                file_sha256=attachment.sha256,
                file_type="docx",
                text="",
                blocks_json=[],
                page_count=None,
                requires_ocr=True,
                read_status=DocumentReadStatus.OCR_REQUIRED,
                error_code="OCR_REQUIRED",
                error_message="测试前置快照",
                reader_version="test-reader",
            )
        )
        session.commit()
    client.app.state.ocr_engine = MockOcrEngine()

    result = client.post(f"/api/tasks/{task['id']}/ocr").json()

    assert result["error_code"] == "OCR_FILE_UNSUPPORTED"
    assert list_snapshots(client, task["id"])[-1]["error_code"] == "OCR_FILE_UNSUPPORTED"
    assert client.get(f"/api/tasks/{task['id']}").json()["task_status"] == "blocked"


def test_missing_ocr_input_is_saved_and_blocks(
    client: TestClient, tmp_path: Path
) -> None:
    image_path = tmp_path / "missing-after-read.png"
    image_path.write_bytes((FIXTURE_DIR / "contract_image.png").read_bytes())
    task_id, _ = create_ocr_required_task(client, image_path, "png")
    image_path.unlink()
    client.app.state.ocr_engine = MockOcrEngine()

    result = client.post(f"/api/tasks/{task_id}/ocr").json()

    assert result["error_code"] == "OCR_INPUT_FILE_NOT_FOUND"
    assert list_snapshots(client, task_id)[-1]["error_code"] == "OCR_INPUT_FILE_NOT_FOUND"
    assert client.get(f"/api/tasks/{task_id}").json()["task_status"] == "blocked"
