from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api.review_rules import router as review_rules_router
from app.api.tasks import router as tasks_router
from app.core.database import create_database, create_tables
from app.integrations.approval import ApprovalGateway, MockApprovalGateway
from app.integrations.ocr import (
    OcrEngine,
    PdfPageRenderer,
    PyMuPdfPageRenderer,
    RapidOcrEngine,
)
from app.parsers import ContractExtractor, DeterministicContractExtractor
from app.rules import DeterministicRuleEngine, RuleEngine, seed_default_review_rules
from app.services.review_result_builder import (
    DeterministicReviewResultBuilder,
    ReviewResultBuilder,
)


DEFAULT_DATABASE_URL = f"sqlite:///{Path('contract_assistant.db').resolve().as_posix()}"


def create_app(
    database_url: str = DEFAULT_DATABASE_URL,
    approval_gateway: ApprovalGateway | None = None,
    contract_storage_root: Path | None = None,
    ocr_engine: OcrEngine | None = None,
    pdf_page_renderer: PdfPageRenderer | None = None,
    contract_extractor: ContractExtractor | None = None,
    rule_engine: RuleEngine | None = None,
    review_result_builder: ReviewResultBuilder | None = None,
) -> FastAPI:
    engine, session_factory = create_database(database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        create_tables(engine)
        with session_factory.begin() as session:
            seed_default_review_rules(session)
        yield
        engine.dispose()

    app = FastAPI(title="合同审批审查系统", version="0.1.0", lifespan=lifespan)
    app.state.session_factory = session_factory
    app.state.approval_gateway = approval_gateway or MockApprovalGateway()
    app.state.contract_storage_root = (
        contract_storage_root or Path("storage/contracts")
    ).resolve()
    app.state.ocr_engine = ocr_engine or RapidOcrEngine()
    app.state.pdf_page_renderer = pdf_page_renderer or PyMuPdfPageRenderer()
    app.state.contract_extractor = contract_extractor or DeterministicContractExtractor()
    app.state.rule_engine = rule_engine or DeterministicRuleEngine()
    app.state.review_result_builder = (
        review_result_builder or DeterministicReviewResultBuilder()
    )
    app.include_router(tasks_router)
    app.include_router(review_rules_router)
    return app


app = create_app()
