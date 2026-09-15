from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import (
    ApprovalAttachment,
    ApprovalTask,
    CommentLog,
    ContractParse,
    DocumentReadSnapshot,
    ReviewResult,
    ReviewRule,
    RuleHit,
)
from scripts.run_contract_demo import run_demo, upgrade_database
from scripts.seed_review_rules import seed_review_rules


EXPECTED_TABLES = {
    "approval_tasks",
    "approval_attachments",
    "document_read_snapshots",
    "contract_parses",
    "review_rules",
    "rule_hits",
    "review_results",
    "review_result_rule_hits",
    "comment_logs",
    "task_logs",
}


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_empty_database_upgrade_creates_all_tables_constraints_and_foreign_keys(
    tmp_path: Path,
) -> None:
    database_url = _database_url(tmp_path / "migration.db")
    upgrade_database(database_url)
    engine = create_engine(database_url)
    database_inspector = inspect(engine)

    assert EXPECTED_TABLES <= set(database_inspector.get_table_names())
    task_unique = {
        tuple(item["column_names"])
        for item in database_inspector.get_unique_constraints("approval_tasks")
    }
    attachment_unique = {
        tuple(item["column_names"])
        for item in database_inspector.get_unique_constraints("approval_attachments")
    }
    rule_hit_unique = {
        tuple(item["column_names"])
        for item in database_inspector.get_unique_constraints("rule_hits")
    }
    assert ("instance_id",) in task_unique
    assert ("approval_code",) in task_unique
    assert ("task_id", "external_attachment_id") in attachment_unique
    assert ("contract_parse_id", "rule_id", "rule_version") in rule_hit_unique

    foreign_key_targets = {
        item["referred_table"]
        for table_name in EXPECTED_TABLES
        for item in database_inspector.get_foreign_keys(table_name)
    }
    assert {
        "approval_tasks",
        "approval_attachments",
        "document_read_snapshots",
        "contract_parses",
        "review_rules",
        "review_results",
        "rule_hits",
    } <= foreign_key_targets
    engine.dispose()


def test_seed_is_idempotent_and_does_not_overwrite_manual_configuration(
    tmp_path: Path,
) -> None:
    database_url = _database_url(tmp_path / "seed.db")
    upgrade_database(database_url)

    assert seed_review_rules(database_url) == (9, 9)
    engine = create_engine(database_url)
    with Session(engine) as session, session.begin():
        rule = session.scalar(
            select(ReviewRule).where(
                ReviewRule.rule_code == "SUBJECT_INFO_MISSING"
            )
        )
        assert rule is not None
        rule.rule_name = "人工调整后的主体规则"

    assert seed_review_rules(database_url) == (0, 9)
    with Session(engine) as session:
        rules = list(session.scalars(select(ReviewRule)))
        subject_rule = next(
            rule for rule in rules if rule.rule_code == "SUBJECT_INFO_MISSING"
        )
        assert len(rules) == 9
        assert subject_rule.rule_name == "人工调整后的主体规则"
    engine.dispose()


def test_formal_app_startup_does_not_create_schema(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path / "formal-startup.db")
    app = create_app(database_url=database_url)
    with TestClient(app):
        pass
    engine = create_engine(database_url)
    assert inspect(engine).get_table_names() == []
    engine.dispose()


def test_demo_runs_from_empty_database_and_second_run_reuses_results(
    tmp_path: Path,
) -> None:
    database_url = _database_url(tmp_path / "demo.db")
    storage_root = tmp_path / "storage"

    first = run_demo(database_url, storage_root)
    second = run_demo(database_url, storage_root)

    assert first.task_status == "done"
    assert first.write_status == "success"
    assert first.overall_risk_level == "high"
    assert first.rule_hit_count >= 2
    assert second == replace(first, reused_completed_flow=True)

    engine = create_engine(database_url)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ApprovalTask)) == 1
        assert session.scalar(select(func.count()).select_from(ApprovalAttachment)) == 1
        assert session.scalar(select(func.count()).select_from(DocumentReadSnapshot)) == 1
        assert session.scalar(select(func.count()).select_from(ContractParse)) == 1
        assert session.scalar(select(func.count()).select_from(ReviewRule)) == 9
        assert session.scalar(select(func.count()).select_from(RuleHit)) == first.rule_hit_count
        assert session.scalar(select(func.count()).select_from(ReviewResult)) == 1
        assert session.scalar(select(func.count()).select_from(CommentLog)) == 1
    engine.dispose()
