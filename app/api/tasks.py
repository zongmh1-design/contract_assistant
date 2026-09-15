from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.database import session_scope
from app.core.task_state import InvalidTaskStateError
from app.integrations.approval import ApprovalGateway
from app.models import ApprovalAttachment, ApprovalTask, CommentLog, TaskLog
from app.repositories import (
    AttachmentRepository,
    ContractParseRepository,
    DocumentReadRepository,
    CommentLogRepository,
    RuleHitRepository,
    ReviewResultRepository,
    TaskRepository,
)
from app.schemas import (
    ApprovalAttachmentRead,
    ApprovalTaskRead,
    AttachmentPreparationResponse,
    ContractParseRead,
    DocumentReadResult,
    DocumentReadSnapshotRead,
    SyncTasksResponse,
    TaskLogRead,
    RuleHitRead,
    RuleReviewResponse,
    ReviewResultRead,
    CommentLogRead,
    CommentWritebackResponse,
)
from app.services.attachment_preparation_service import (
    AttachmentPreparationError,
    AttachmentPreparationService,
)
from app.services.contract_extraction_service import ContractExtractionService
from app.services.approval_sync_service import (
    ApprovalIdentityConflictError,
    ApprovalSyncService,
)
from app.services.mock_failure_service import (
    MockFailureNotConfiguredError,
    MockFailureService,
)
from app.services.document_reading_service import DocumentReadingService
from app.services.document_ocr_service import (
    DocumentOcrNotAllowedError,
    DocumentOcrService,
)
from app.services.document_source_selector import DocumentReadSnapshotNotFoundError
from app.services.task_state_service import TaskNotFoundError, TaskStateService
from app.services.contract_parse_selector import ContractParseNotFoundError
from app.services.contract_review_service import (
    ContractReviewService,
    RuleEngineFailedError,
)
from app.services.review_result_service import (
    ReviewResultGenerationError,
    ReviewResultService,
    RuleReviewNotFoundError,
)
from app.services.comment_writeback_service import (
    CommentWritebackError,
    CommentWritebackService,
)
from app.services.current_review_result_selector import (
    CurrentReviewResultNotFoundError,
)
from app.services.llm_assisted_contract_extraction_service import (
    LlmAssistedContractExtractionService,
    LlmProviderNotConfiguredError,
)
from app.services.task_retry_service import RetryStageNotSupportedError, TaskRetryService


router = APIRouter(prefix="/api/tasks", tags=["approval-tasks"])


def get_session(request: Request) -> Iterator[Session]:
    yield from session_scope(request.app.state.session_factory)


def get_gateway(request: Request) -> ApprovalGateway:
    return request.app.state.approval_gateway


SessionDependency = Annotated[Session, Depends(get_session)]
GatewayDependency = Annotated[ApprovalGateway, Depends(get_gateway)]


def raise_http_error(error: Exception) -> None:
    if isinstance(error, TaskNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    if isinstance(
        error,
        (
            InvalidTaskStateError,
            ApprovalIdentityConflictError,
            MockFailureNotConfiguredError,
            AttachmentPreparationError,
            DocumentOcrNotAllowedError,
            DocumentReadSnapshotNotFoundError,
            ContractParseNotFoundError,
            RuleEngineFailedError,
            ReviewResultGenerationError,
            RuleReviewNotFoundError,
            CommentWritebackError,
            CurrentReviewResultNotFoundError,
            RetryStageNotSupportedError,
            LlmProviderNotConfiguredError,
        ),
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error


@router.post("/sync", response_model=SyncTasksResponse)
def sync_mock_tasks(
    session: SessionDependency,
    gateway: GatewayDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> SyncTasksResponse:
    try:
        result = ApprovalSyncService(session, gateway).sync_pending_tasks(limit)
        return SyncTasksResponse(
            created_count=result.created_count,
            updated_count=result.updated_count,
            tasks=[ApprovalTaskRead.model_validate(task) for task in result.tasks],
        )
    except ApprovalIdentityConflictError as error:
        session.rollback()
        raise_http_error(error)


@router.get("", response_model=list[ApprovalTaskRead])
def list_tasks(session: SessionDependency) -> list[ApprovalTask]:
    return TaskRepository(session).list_tasks()


@router.get("/{task_id}", response_model=ApprovalTaskRead)
def get_task(task_id: int, session: SessionDependency) -> ApprovalTask:
    task = TaskRepository(session).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return task


@router.post("/{task_id}/start-parsing", response_model=ApprovalTaskRead)
def start_parsing(task_id: int, session: SessionDependency) -> ApprovalTask:
    try:
        return TaskStateService(session).start_parsing(task_id)
    except (TaskNotFoundError, InvalidTaskStateError) as error:
        session.rollback()
        raise_http_error(error)


@router.post("/{task_id}/simulate-parsing-failure", response_model=ApprovalTaskRead)
def simulate_parsing_failure(
    task_id: int, session: SessionDependency, gateway: GatewayDependency
) -> ApprovalTask:
    try:
        return MockFailureService(session, gateway).simulate_parsing_failure(task_id)
    except (
        TaskNotFoundError,
        InvalidTaskStateError,
        MockFailureNotConfiguredError,
        ValueError,
    ) as error:
        session.rollback()
        raise_http_error(error)


@router.post("/{task_id}/retry", response_model=ApprovalTaskRead)
def retry_task(
    task_id: int,
    request: Request,
    session: SessionDependency,
    gateway: GatewayDependency,
) -> ApprovalTask:
    try:
        return TaskRetryService(
            session,
            gateway,
            request.app.state.contract_storage_root,
        ).retry(task_id)
    except (
        TaskNotFoundError,
        InvalidTaskStateError,
        AttachmentPreparationError,
        ValueError,
        CommentWritebackError,
        CurrentReviewResultNotFoundError,
        RetryStageNotSupportedError,
    ) as error:
        session.rollback()
        raise_http_error(error)


@router.post(
    "/{task_id}/prepare-attachment",
    response_model=AttachmentPreparationResponse,
)
def prepare_attachment(
    task_id: int,
    request: Request,
    session: SessionDependency,
    gateway: GatewayDependency,
) -> AttachmentPreparationResponse:
    try:
        result = AttachmentPreparationService(
            session,
            gateway,
            request.app.state.contract_storage_root,
        ).prepare_main_attachment(task_id)
        return AttachmentPreparationResponse(
            event=result.event,
            task=ApprovalTaskRead.model_validate(result.task),
            attachment=(
                ApprovalAttachmentRead.model_validate(result.attachment)
                if result.attachment is not None
                else None
            ),
        )
    except (TaskNotFoundError, InvalidTaskStateError, AttachmentPreparationError) as error:
        session.rollback()
        raise_http_error(error)


@router.get(
    "/{task_id}/attachments",
    response_model=list[ApprovalAttachmentRead],
)
def list_task_attachments(
    task_id: int, session: SessionDependency
) -> list[ApprovalAttachment]:
    if TaskRepository(session).get_task(task_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return AttachmentRepository(session).list_for_task(task_id)


@router.post("/{task_id}/read-document", response_model=DocumentReadResult)
def read_document(task_id: int, session: SessionDependency) -> DocumentReadResult:
    try:
        return DocumentReadingService(session).read_main_document(task_id)
    except (TaskNotFoundError, InvalidTaskStateError) as error:
        session.rollback()
        raise_http_error(error)


@router.post("/{task_id}/ocr", response_model=DocumentReadResult)
def ocr_document(
    task_id: int, request: Request, session: SessionDependency
) -> DocumentReadResult:
    try:
        return DocumentOcrService(
            session,
            request.app.state.ocr_engine,
            request.app.state.pdf_page_renderer,
        ).recognize_main_document(task_id)
    except (TaskNotFoundError, InvalidTaskStateError, DocumentOcrNotAllowedError) as error:
        session.rollback()
        raise_http_error(error)


@router.post("/{task_id}/parse-contract", response_model=ContractParseRead)
def parse_contract(
    task_id: int, request: Request, session: SessionDependency
) -> ContractParseRead:
    try:
        result = ContractExtractionService(
            session, request.app.state.contract_extractor
        ).parse_contract(task_id)
        return ContractParseRead.model_validate(result)
    except (
        TaskNotFoundError,
        InvalidTaskStateError,
        DocumentReadSnapshotNotFoundError,
    ) as error:
        session.rollback()
        raise_http_error(error)


@router.post(
    "/{task_id}/parse-contract/llm-assist", response_model=ContractParseRead
)
def parse_contract_with_llm(
    task_id: int, request: Request, session: SessionDependency
):
    provider = request.app.state.llm_provider
    if provider is None:
        raise_http_error(
            LlmProviderNotConfiguredError("LLM_PROVIDER_NOT_CONFIGURED")
        )
    try:
        return LlmAssistedContractExtractionService(
            session, provider
        ).parse_contract(task_id)
    except Exception as error:
        session.rollback()
        raise_http_error(error)


@router.get(
    "/{task_id}/document-reads",
    response_model=list[DocumentReadSnapshotRead],
)
def list_document_reads(
    task_id: int, session: SessionDependency
) -> list[DocumentReadSnapshotRead]:
    if TaskRepository(session).get_task(task_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    snapshots = DocumentReadRepository(session).list_for_task(task_id)
    return [DocumentReadSnapshotRead.model_validate(item) for item in snapshots]


@router.get(
    "/{task_id}/contract-parses",
    response_model=list[ContractParseRead],
)
def list_contract_parses(
    task_id: int, session: SessionDependency
) -> list[ContractParseRead]:
    if TaskRepository(session).get_task(task_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    parses = ContractParseRepository(session).list_for_task(task_id)
    return [ContractParseRead.model_validate(item) for item in parses]


@router.post("/{task_id}/run-rules", response_model=RuleReviewResponse)
def run_rules(
    task_id: int, request: Request, session: SessionDependency
) -> RuleReviewResponse:
    try:
        return ContractReviewService(
            session, request.app.state.rule_engine
        ).run_rules(task_id)
    except (
        TaskNotFoundError,
        InvalidTaskStateError,
        ContractParseNotFoundError,
        RuleEngineFailedError,
    ) as error:
        session.rollback()
        raise_http_error(error)


@router.get("/{task_id}/rule-hits", response_model=list[RuleHitRead])
def list_rule_hits(
    task_id: int, session: SessionDependency
) -> list[RuleHitRead]:
    if TaskRepository(session).get_task(task_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    hits = RuleHitRepository(session).list_for_task(task_id)
    return [RuleHitRead.from_models(hit, hit.rule) for hit in hits]


@router.post("/{task_id}/review-results", response_model=ReviewResultRead)
def create_review_result(
    task_id: int, request: Request, session: SessionDependency
) -> ReviewResultRead:
    try:
        return ReviewResultService(
            session, request.app.state.review_result_builder
        ).generate(task_id)
    except (
        TaskNotFoundError,
        InvalidTaskStateError,
        ContractParseNotFoundError,
        RuleReviewNotFoundError,
        ReviewResultGenerationError,
    ) as error:
        session.rollback()
        raise_http_error(error)


@router.get("/{task_id}/review-results", response_model=list[ReviewResultRead])
def list_review_results(
    task_id: int, session: SessionDependency
) -> list[ReviewResultRead]:
    if TaskRepository(session).get_task(task_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    results = ReviewResultRepository(session).list_for_task(task_id)
    return [ReviewResultRead.from_model(result) for result in results]


@router.post("/{task_id}/write-comment", response_model=CommentWritebackResponse)
def write_comment(
    task_id: int, session: SessionDependency, gateway: GatewayDependency
) -> CommentWritebackResponse:
    try:
        result = CommentWritebackService(session, gateway).write_comment(task_id)
        return CommentWritebackResponse(
            reused=result.reused,
            task=ApprovalTaskRead.model_validate(result.task),
            comment_log=CommentLogRead.model_validate(result.comment_log),
        )
    except (
        TaskNotFoundError,
        InvalidTaskStateError,
        CurrentReviewResultNotFoundError,
        CommentWritebackError,
    ) as error:
        session.rollback()
        raise_http_error(error)


@router.get("/{task_id}/comment-logs", response_model=list[CommentLogRead])
def list_comment_logs(
    task_id: int, session: SessionDependency
) -> list[CommentLog]:
    if TaskRepository(session).get_task(task_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return CommentLogRepository(session).list_for_task(task_id)


@router.get("/{task_id}/logs", response_model=list[TaskLogRead])
def list_task_logs(task_id: int, session: SessionDependency) -> list[TaskLog]:
    repository = TaskRepository(session)
    if repository.get_task(task_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return repository.list_logs(task_id)
