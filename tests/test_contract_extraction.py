import re
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.integrations.ocr import PyMuPdfPageRenderer, RapidOcrEngine
from app.models import (
    ApprovalAttachment,
    ContractParse,
    DocumentReadSnapshot,
    DocumentReadStatus,
    DownloadStatus,
)
from app.parsers import DeterministicContractExtractor, MockContractExtractor
from app.services.document_source_selector import DocumentSourceSelector


FIXTURE_DIR = Path(__file__).parent / "fixtures"
BASIC_FIELD_NAMES = (
    "contract_title",
    "contract_number",
    "signing_party",
    "counterparty",
    "amount",
    "currency",
    "effective_date",
    "expiry_date",
)
CLAUSE_NAMES = (
    "payment_clause",
    "delivery_clause",
    "acceptance_clause",
    "breach_clause",
    "confidentiality_clause",
    "data_clause",
    "intellectual_property_clause",
    "dispute_resolution_clause",
)


def chinese_contract_blocks(*, with_pages: bool = True) -> list[dict]:
    paragraphs = [
        text.strip()
        for text in (FIXTURE_DIR / "chinese_contract.txt").read_text(
            encoding="utf-8"
        ).split("\n\n")
        if text.strip()
    ]
    return [
        {
            "text": text,
            "page_number": (1 if index <= 8 else 2) if with_pages else None,
            "block_index": index,
        }
        for index, text in enumerate(paragraphs)
    ]


def create_task_with_snapshot(
    client: TestClient,
    blocks: list[dict] | None = None,
    *,
    file_sha256: str = "a" * 64,
) -> tuple[int, int, int]:
    task = client.post("/api/tasks/sync", params={"limit": 1}).json()["tasks"][0]
    client.post(f"/api/tasks/{task['id']}/start-parsing")
    blocks = blocks if blocks is not None else chinese_contract_blocks()
    with client.app.state.session_factory() as session:
        attachment = ApprovalAttachment(
            task_id=task["id"],
            external_attachment_id="structured-contract",
            original_file_name="虚构软件采购合同.pdf",
            file_type="pdf",
            file_path=str((FIXTURE_DIR / "text_contract.pdf").resolve()),
            file_size=100,
            sha256=file_sha256,
            is_main_contract=True,
            download_status=DownloadStatus.SUCCESS,
        )
        session.add(attachment)
        session.flush()
        snapshot = DocumentReadSnapshot(
            attachment_id=attachment.id,
            read_method="pdf_text",
            file_sha256=file_sha256,
            file_type="pdf",
            text="\n\n".join(block["text"] for block in blocks),
            blocks_json=blocks,
            page_count=2 if any(block["page_number"] == 2 for block in blocks) else None,
            requires_ocr=False,
            read_status=DocumentReadStatus.SUCCESS,
            error_code=None,
            error_message=None,
            reader_version="fixture-reader-1",
        )
        session.add(snapshot)
        session.commit()
        return task["id"], attachment.id, snapshot.id


def test_source_selector_uses_latest_success_for_current_sha(
    client: TestClient,
) -> None:
    task_id, attachment_id, first_snapshot_id = create_task_with_snapshot(client)
    with client.app.state.session_factory() as session:
        session.add(
            DocumentReadSnapshot(
                attachment_id=attachment_id,
                read_method="ocr",
                file_sha256="b" * 64,
                file_type="pdf",
                text="旧文件文本",
                blocks_json=[],
                page_count=1,
                requires_ocr=False,
                read_status=DocumentReadStatus.SUCCESS,
                reader_version="old",
            )
        )
        session.add(
            DocumentReadSnapshot(
                attachment_id=attachment_id,
                read_method="ocr",
                file_sha256="a" * 64,
                file_type="pdf",
                text="当前文件最新 OCR 文本",
                blocks_json=chinese_contract_blocks(),
                page_count=2,
                requires_ocr=False,
                read_status=DocumentReadStatus.SUCCESS,
                reader_version="new",
            )
        )
        session.commit()
        selected = DocumentSourceSelector(session).select_for_task(task_id)

        assert selected.id > first_snapshot_id
        assert selected.file_sha256 == "a" * 64
        assert selected.text == "当前文件最新 OCR 文本"


def test_basic_fields_are_extracted_with_evidence(client: TestClient) -> None:
    task_id, _, snapshot_id = create_task_with_snapshot(client)

    response = client.post(f"/api/tasks/{task_id}/parse-contract")

    assert response.status_code == 200
    result = response.json()
    basic = result["basic_info_json"]
    assert result["document_read_snapshot_id"] == snapshot_id
    assert result["parse_status"] == "success"
    assert basic["contract_title"]["value"] == "软件采购合同"
    assert basic["contract_number"]["value"] == "HT-2026-0098"
    assert basic["signing_party"]["value"].startswith("星河科技有限公司")
    assert basic["counterparty"]["value"].startswith("远航软件服务有限公司")
    assert basic["amount"]["value"] == "500000"
    assert basic["currency"]["value"] == "CNY"
    assert basic["effective_date"]["value"] == "2026-10-01"
    assert basic["expiry_date"]["value"] == "2027-09-30"
    assert basic["amount"]["source_text"] == "合同总金额为人民币500000元整。"
    assert basic["amount"]["position"]["block_start"] == 4
    assert basic["amount"]["position"]["page_start"] == 1


def test_clauses_include_cross_block_evidence_and_positions(
    client: TestClient,
) -> None:
    task_id, _, _ = create_task_with_snapshot(client)

    clauses = client.post(f"/api/tasks/{task_id}/parse-contract").json()[
        "clause_info_json"
    ]

    payment = clauses["payment_clause"]
    assert payment["extract_status"] == "found"
    assert "第一条 付款条款" in payment["source_text"]
    assert "剩余款项" in payment["source_text"]
    assert payment["position"]["block_start"] == 7
    assert payment["position"]["block_end"] == 9
    assert payment["position"]["page_start"] == 1
    assert payment["position"]["page_end"] == 2
    assert clauses["breach_clause"]["extract_status"] == "found"
    assert "每日万分之五" in clauses["breach_clause"]["source_text"]
    assert all(clauses[name]["extract_status"] == "found" for name in CLAUSE_NAMES)


def test_docx_positions_do_not_invent_page_numbers(client: TestClient) -> None:
    task_id, _, _ = create_task_with_snapshot(
        client, chinese_contract_blocks(with_pages=False)
    )

    result = client.post(f"/api/tasks/{task_id}/parse-contract").json()

    for field_name in BASIC_FIELD_NAMES:
        position = result["basic_info_json"][field_name]["position"]
        assert position is None or position["page_start"] is None
    payment_position = result["clause_info_json"]["payment_clause"]["position"]
    assert payment_position["page_start"] is None
    assert payment_position["page_end"] is None


def test_missing_field_produces_partial_without_blocking(client: TestClient) -> None:
    blocks = [
        block
        for block in chinese_contract_blocks()
        if "合同编号" not in block["text"]
    ]
    for index, block in enumerate(blocks):
        block["block_index"] = index
    task_id, _, _ = create_task_with_snapshot(client, blocks)

    result = client.post(f"/api/tasks/{task_id}/parse-contract").json()

    assert result["parse_status"] == "partial"
    assert result["basic_info_json"]["contract_number"]["extract_status"] == "not_found"
    assert client.get(f"/api/tasks/{task_id}").json()["task_status"] == "parsing"


def test_distinct_field_candidates_are_ambiguous(client: TestClient) -> None:
    blocks = chinese_contract_blocks()
    blocks.insert(
        2,
        {
            "text": "合同编号：HT-OTHER-0001",
            "page_number": 1,
            "block_index": 2,
        },
    )
    for index, block in enumerate(blocks):
        block["block_index"] = index
    task_id, _, _ = create_task_with_snapshot(client, blocks)

    result = client.post(f"/api/tasks/{task_id}/parse-contract").json()
    contract_number = result["basic_info_json"]["contract_number"]

    assert result["parse_status"] == "partial"
    assert contract_number["value"] is None
    assert contract_number["extract_status"] == "ambiguous"
    assert "HT-2026-0098" in contract_number["source_text"]
    assert "HT-OTHER-0001" in contract_number["source_text"]


def test_invalid_date_marks_field_failed_without_blocking(client: TestClient) -> None:
    blocks = chinese_contract_blocks()
    effective_date_block = next(
        block for block in blocks if "生效日期" in block["text"]
    )
    effective_date_block["text"] = "生效日期：2026年99月99日"
    task_id, _, _ = create_task_with_snapshot(client, blocks)

    result = client.post(f"/api/tasks/{task_id}/parse-contract").json()

    assert result["parse_status"] == "partial"
    assert result["basic_info_json"]["effective_date"]["extract_status"] == "failed"
    assert result["basic_info_json"]["effective_date"]["source_text"] == "生效日期：2026年99月99日"
    assert client.get(f"/api/tasks/{task_id}").json()["task_status"] == "parsing"


def test_extractor_exception_saves_failed_parse_and_blocks(client: TestClient) -> None:
    task_id, _, snapshot_id = create_task_with_snapshot(client)
    client.app.state.contract_extractor = MockContractExtractor(
        failure_message="模拟 Extractor 程序异常"
    )

    response = client.post(f"/api/tasks/{task_id}/parse-contract")

    assert response.status_code == 200
    result = response.json()
    assert result["document_read_snapshot_id"] == snapshot_id
    assert result["parse_status"] == "failed"
    assert "CONTRACT_EXTRACTION_FAILED" in result["parse_error"]
    assert all(
        result["basic_info_json"][name]["extract_status"] == "failed"
        for name in BASIC_FIELD_NAMES
    )
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["task_status"] == "blocked"
    assert task["blocked_stage"] == "field_extraction"


def test_missing_current_snapshot_blocks_without_contract_parse(
    client: TestClient,
) -> None:
    task_id, attachment_id, _ = create_task_with_snapshot(client)
    with client.app.state.session_factory() as session:
        attachment = session.get(ApprovalAttachment, attachment_id)
        assert attachment is not None
        attachment.sha256 = "c" * 64
        session.commit()

    response = client.post(f"/api/tasks/{task_id}/parse-contract")

    assert response.status_code == 409
    assert "DOCUMENT_READ_SNAPSHOT_NOT_FOUND" in response.json()["detail"]
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["task_status"] == "blocked"
    assert task["blocked_stage"] == "field_extraction"
    assert client.get(f"/api/tasks/{task_id}/contract-parses").json() == []


def test_contract_parse_persists_and_query_api_returns_structure(
    client: TestClient,
) -> None:
    task_id, _, _ = create_task_with_snapshot(client)
    created = client.post(f"/api/tasks/{task_id}/parse-contract").json()

    response = client.get(f"/api/tasks/{task_id}/contract-parses")

    assert response.status_code == 200
    assert response.json() == [created]


def test_same_extractor_version_reuses_parse(client: TestClient) -> None:
    task_id, _, _ = create_task_with_snapshot(client)
    with client.app.state.session_factory() as session:
        snapshot = session.scalar(select(DocumentReadSnapshot))
        assert snapshot is not None
        extraction = DeterministicContractExtractor().extract(snapshot)
    extractor = MockContractExtractor(extraction)
    client.app.state.contract_extractor = extractor

    first = client.post(f"/api/tasks/{task_id}/parse-contract").json()
    second = client.post(f"/api/tasks/{task_id}/parse-contract").json()

    assert second == first
    assert extractor.calls == 1
    assert len(client.get(f"/api/tasks/{task_id}/contract-parses").json()) == 1
    logs = client.get(f"/api/tasks/{task_id}/logs").json()
    assert any(log["log_type"] == "CONTRACT_PARSE_REUSED" for log in logs)


def test_extractor_version_change_creates_new_parse_and_keeps_old(
    client: TestClient,
) -> None:
    task_id, _, _ = create_task_with_snapshot(client)
    with client.app.state.session_factory() as session:
        snapshot = session.scalar(select(DocumentReadSnapshot))
        assert snapshot is not None
        extraction = DeterministicContractExtractor().extract(snapshot)

    client.app.state.contract_extractor = MockContractExtractor(
        extraction, name="versioned", version="1.0"
    )
    client.post(f"/api/tasks/{task_id}/parse-contract")
    client.app.state.contract_extractor = MockContractExtractor(
        extraction, name="versioned", version="2.0"
    )
    client.post(f"/api/tasks/{task_id}/parse-contract")

    parses = client.get(f"/api/tasks/{task_id}/contract-parses").json()
    assert len(parses) == 2
    assert [item["extractor_version"] for item in parses] == ["1.0", "2.0"]
    assert parses[0]["id"] != parses[1]["id"]


def test_new_document_snapshot_creates_new_parse_and_keeps_old(
    client: TestClient,
) -> None:
    task_id, attachment_id, first_snapshot_id = create_task_with_snapshot(client)
    first_parse = client.post(f"/api/tasks/{task_id}/parse-contract").json()
    blocks = chinese_contract_blocks()
    with client.app.state.session_factory() as session:
        newer_snapshot = DocumentReadSnapshot(
            attachment_id=attachment_id,
            read_method="ocr",
            file_sha256="a" * 64,
            file_type="pdf",
            text="\n\n".join(block["text"] for block in blocks),
            blocks_json=blocks,
            page_count=2,
            requires_ocr=False,
            read_status=DocumentReadStatus.SUCCESS,
            error_code=None,
            error_message=None,
            reader_version="fixture-reader-2",
        )
        session.add(newer_snapshot)
        session.commit()
        newer_snapshot_id = newer_snapshot.id

    second_parse = client.post(f"/api/tasks/{task_id}/parse-contract").json()
    parses = client.get(f"/api/tasks/{task_id}/contract-parses").json()

    assert first_parse["document_read_snapshot_id"] == first_snapshot_id
    assert second_parse["document_read_snapshot_id"] == newer_snapshot_id
    assert len(parses) == 2
    assert parses[0]["id"] == first_parse["id"]


def test_real_chinese_rapidocr_smoke() -> None:
    pdf_path = FIXTURE_DIR / "chinese_contract_scan.pdf"
    engine = RapidOcrEngine()
    with TemporaryDirectory(prefix="chinese-ocr-smoke-") as temporary_directory:
        pages = PyMuPdfPageRenderer().render_pages(
            pdf_path, Path(temporary_directory)
        )
        recognized_text = "\n".join(
            line.text
            for page in pages
            for line in engine.recognize_image(page.image_path).lines
        )

    for keyword in ("合同", "甲方", "乙方", "人民币", "付款"):
        assert keyword in recognized_text
    assert re.search(r"\d", recognized_text)
