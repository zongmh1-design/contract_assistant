from collections.abc import Iterator
from typing import TypeAlias

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


SessionFactory: TypeAlias = sessionmaker[Session]


def create_database(database_url: str) -> tuple[Engine, SessionFactory]:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def create_tables(engine: Engine) -> None:
    # 导入模型后，SQLAlchemy 才能发现需要创建的表。
    from app.models import ApprovalTask, TaskLog  # noqa: F401

    Base.metadata.create_all(engine)


def session_scope(session_factory: SessionFactory) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
