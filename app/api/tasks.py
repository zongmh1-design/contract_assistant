from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.database import session_scope
from app.core.task_state import InvalidTaskStateError
from app.integrations.approval import ApprovalGateway
from app.models import ApprovalAttachment, ApprovalTask, TaskLog
from app.repositories import (
    AttachmentRepository,
    DocumentReadRepository,
    TaskRepository,
)
from app.schemas import (
    ApprovalAttachmentRead,
    ApprovalTaskRead,
    AttachmentPreparationResponse,
    DocumentReadResult,
    DocumentReadSnapshotRead,
    SyncTasksResponse,
    TaskLogRead,
)
from app.services.attachment_preparation_service import (
    AttachmentPreparationError,
    AttachmentPreparationService,
)
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
from app.services.task_state_service import TaskNotFoundError, TaskStateService


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
        result = AttachmentPreparationService(
            session,
            gateway,
            request.app.state.contract_storage_root,
        ).retry_and_prepare(task_id)
        return result.task
    except (
        TaskNotFoundError,
        InvalidTaskStateError,
        AttachmentPreparationError,
        ValueError,
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


@router.get("/{task_id}/logs", response_model=list[TaskLogRead])
def list_task_logs(task_id: int, session: SessionDependency) -> list[TaskLog]:
    repository = TaskRepository(session)
    if repository.get_task(task_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return repository.list_logs(task_id)
