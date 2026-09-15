from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api.tasks import router as tasks_router
from app.core.database import create_database, create_tables
from app.integrations.approval import ApprovalGateway, MockApprovalGateway


DEFAULT_DATABASE_URL = f"sqlite:///{Path('contract_assistant.db').resolve().as_posix()}"


def create_app(
    database_url: str = DEFAULT_DATABASE_URL,
    approval_gateway: ApprovalGateway | None = None,
    contract_storage_root: Path | None = None,
) -> FastAPI:
    engine, session_factory = create_database(database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        create_tables(engine)
        yield
        engine.dispose()

    app = FastAPI(title="合同审批审查系统", version="0.1.0", lifespan=lifespan)
    app.state.session_factory = session_factory
    app.state.approval_gateway = approval_gateway or MockApprovalGateway()
    app.state.contract_storage_root = (
        contract_storage_root or Path("storage/contracts")
    ).resolve()
    app.include_router(tasks_router)
    return app


app = create_app()
