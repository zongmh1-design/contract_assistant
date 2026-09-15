from __future__ import annotations

import argparse

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.core.database import create_database
from app.models import ReviewRule
from app.rules import seed_default_review_rules


DEFAULT_DATABASE_URL = "sqlite:///contract_assistant.db"


def seed_review_rules(database_url: str = DEFAULT_DATABASE_URL) -> tuple[int, int]:
    """补充缺失的默认规则，保留已有规则的人工配置。"""

    engine, session_factory = create_database(database_url)
    try:
        with session_factory.begin() as session:
            created_count = seed_default_review_rules(session)
            total_count = session.scalar(select(func.count()).select_from(ReviewRule)) or 0
        return created_count, total_count
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="幂等写入默认合同审查规则")
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    args = parser.parse_args()
    try:
        created_count, total_count = seed_review_rules(args.database_url)
    except OperationalError as error:
        raise SystemExit(
            "规则表不可用，请先执行 `uv run alembic upgrade head`。"
        ) from error
    print(f"默认规则 seed 完成：新增 {created_count} 条，当前共 {total_count} 条。")


if __name__ == "__main__":
    main()
