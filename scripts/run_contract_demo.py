from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core.database import create_database
from app.integrations.approval import MockApprovalGateway
from app.models import ApprovalTask, TaskStatus, WriteStatus
from app.repositories import (
    AttachmentRepository,
    CommentLogRepository,
    RuleHitRepository,
    TaskRepository,
)
from app.services.approval_sync_service import ApprovalSyncService
from app.services.attachment_preparation_service import AttachmentPreparationService
from app.services.comment_writeback_service import CommentWritebackService
from app.services.contract_extraction_service import ContractExtractionService
from app.services.contract_parse_selector import ContractParseSelector
from app.services.contract_review_service import ContractReviewService
from app.services.current_review_result_selector import CurrentReviewResultSelector
from app.services.document_reading_service import DocumentReadingService
from app.services.document_source_selector import DocumentSourceSelector
from app.services.review_result_service import ReviewResultService
from scripts.seed_review_rules import seed_review_rules


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEMO_DATABASE = PROJECT_ROOT / "demo_contract_assistant.db"
DEFAULT_DEMO_DATABASE_URL = f"sqlite:///{DEFAULT_DEMO_DATABASE.as_posix()}"
DEFAULT_DEMO_STORAGE = PROJECT_ROOT / "storage" / "demo_contracts"
DEMO_CONTRACT = PROJECT_ROOT / "sample_data" / "demo_procurement_contract.pdf"


@dataclass(frozen=True)
class DemoSummary:
    task_id: int
    approval_code: str
    contract_parse_id: int
    overall_risk_level: str
    rule_hit_count: int
    review_result_id: int
    external_comment_id: str
    task_status: str
    write_status: str
    reused_completed_flow: bool


def upgrade_database(database_url: str) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    config.attributes["database_url_override"] = database_url
    config.set_section_option("logger_alembic", "level", "WARN")
    command.upgrade(config, "head")


def run_demo(
    database_url: str = DEFAULT_DEMO_DATABASE_URL,
    storage_root: Path = DEFAULT_DEMO_STORAGE,
) -> DemoSummary:
    if not DEMO_CONTRACT.is_file():
        raise FileNotFoundError(f"演示合同不存在: {DEMO_CONTRACT}")

    upgrade_database(database_url)
    seed_review_rules(database_url)
    gateway = MockApprovalGateway()
    gateway.set_attachment_content(
        "MOCK-INSTANCE-001", "att_001", DEMO_CONTRACT.read_bytes()
    )
    engine, session_factory = create_database(database_url)
    try:
        with session_factory() as session:
            print("[1/8] 拉取审批任务")
            sync_result = ApprovalSyncService(session, gateway).sync_pending_tasks(limit=1)
            task = next(
                item
                for item in sync_result.tasks
                if item.instance_id == "MOCK-INSTANCE-001"
            )
            print(f"Approval: {task.approval_code}")

            if task.task_status == TaskStatus.DONE:
                return _report_completed_demo(session, task, reused=True)

            print("\n[2/8] 下载合同附件")
            attachment_result = AttachmentPreparationService(
                session, gateway, storage_root
            ).prepare_main_attachment(task.id)
            attachment = attachment_result.attachment
            if attachment is None:
                raise RuntimeError(f"演示附件准备失败: {attachment_result.event}")
            print(f"File: {attachment.file_path}")

            print("\n[3/8] 读取合同")
            read_result = DocumentReadingService(session).read_main_document(task.id)
            if read_result.requires_ocr:
                raise RuntimeError("文本型演示合同不应进入 OCR")
            source = DocumentSourceSelector(session).select_for_task(task.id)
            print(f"Method: {source.read_method}")

            print("\n[4/8] 提取合同字段")
            contract_parse = ContractExtractionService(session).parse_contract(task.id)
            basic_info = contract_parse.basic_info_json
            print(f"Amount: {_fact_value(basic_info, 'amount')}")
            print(f"Party A: {_fact_value(basic_info, 'signing_party')}")
            print(f"Party B: {_fact_value(basic_info, 'counterparty')}")

            print("\n[5/8] 规则审查")
            review = ContractReviewService(session).run_rules(task.id)
            print(f"Hits: {review.summary.hit_count}")

            print("\n[6/8] 生成审查结果")
            review_result = ReviewResultService(session).generate(task.id)
            print(f"Risk: {review_result.overall_risk_level.value.upper()}")

            print("\n[7/8] 写回评论")
            writeback = CommentWritebackService(session, gateway).write_comment(task.id)
            print(f"Comment ID: {writeback.comment_log.external_comment_id}")

            completed_task = TaskRepository(session).get_task(task.id)
            assert completed_task is not None
            return _report_completed_demo(session, completed_task, reused=False)
    finally:
        engine.dispose()


def _report_completed_demo(session, task: ApprovalTask, reused: bool) -> DemoSummary:
    attachment = AttachmentRepository(session).get_main_downloaded_attachment(task.id)
    if attachment is None:
        raise RuntimeError("已完成任务缺少主合同附件")
    document_read = DocumentSourceSelector(session).select_for_task(task.id)
    contract_parse = ContractParseSelector(session).select_for_task(task.id)
    hits = RuleHitRepository(session).list_current_for_parse(contract_parse.id)
    review_result = CurrentReviewResultSelector(session).select_for_task(task.id)
    comment_log = CommentLogRepository(session).get_success_for_review_result(
        review_result.id
    )
    if comment_log is None or not comment_log.external_comment_id:
        raise RuntimeError("已完成任务缺少成功评论回写记录")

    if reused:
        print("\n[2/8] 下载合同附件")
        print(f"File: {attachment.file_path} (reused)")
        print("\n[3/8] 读取合同")
        print(f"Method: {document_read.read_method} (reused)")
        print("\n[4/8] 提取合同字段")
        print(f"Contract Parse: {contract_parse.id} (reused)")
        print("\n[5/8] 规则审查")
        print(f"Hits: {len(hits)} (reused)")
        print("\n[6/8] 生成审查结果")
        print(f"Risk: {review_result.overall_risk_level.value.upper()} (reused)")
        print("\n[7/8] 写回评论")
        print(f"Comment ID: {comment_log.external_comment_id} (reused)")

    print("\n[8/8] 完成")
    print(f"Task Status: {task.task_status.value}")
    print(f"Write Status: {task.write_status.value}")
    print("\n=== Demo 最终摘要 ===")
    summary = DemoSummary(
        task_id=task.id,
        approval_code=task.approval_code,
        contract_parse_id=contract_parse.id,
        overall_risk_level=review_result.overall_risk_level.value,
        rule_hit_count=len(hits),
        review_result_id=review_result.id,
        external_comment_id=comment_log.external_comment_id,
        task_status=task.task_status.value,
        write_status=task.write_status.value,
        reused_completed_flow=reused,
    )
    for field_name, value in summary.__dict__.items():
        if field_name != "reused_completed_flow":
            print(f"{field_name}: {value}")
    return summary


def _fact_value(basic_info: dict, field_name: str) -> str:
    return str(basic_info[field_name].get("value") or "未提取")


def _reset_default_demo_database(database_url: str) -> None:
    if database_url != DEFAULT_DEMO_DATABASE_URL:
        raise ValueError("--reset 仅允许清理项目专用的默认 Demo 数据库")
    resolved = DEFAULT_DEMO_DATABASE.resolve()
    if resolved.parent != PROJECT_ROOT or resolved.name != "demo_contract_assistant.db":
        raise RuntimeError("Demo 数据库路径安全校验失败")
    if resolved.is_file():
        resolved.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="运行合同审批完整 Mock Demo")
    parser.add_argument("--database-url", default=DEFAULT_DEMO_DATABASE_URL)
    parser.add_argument("--storage-root", type=Path, default=DEFAULT_DEMO_STORAGE)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    if args.reset:
        _reset_default_demo_database(args.database_url)
    run_demo(args.database_url, args.storage_root)


if __name__ == "__main__":
    main()
