from dataclasses import dataclass
from typing import NoReturn

from sqlalchemy.orm import Session

from app.core.task_state import InvalidTaskStateError
from app.integrations.approval import ApprovalGateway, CommentWriteError
from app.models import ApprovalTask, CommentLog, TaskStatus, WriteStatus
from app.repositories import CommentLogRepository, TaskRepository
from app.services.current_review_result_selector import (
    CurrentReviewResultNotFoundError,
    CurrentReviewResultSelector,
)
from app.services.task_state_service import TaskNotFoundError, TaskStateService


class CommentWritebackError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommentWritebackResult:
    task: ApprovalTask
    comment_log: CommentLog
    reused: bool


class CommentWritebackService:
    def __init__(self, session: Session, gateway: ApprovalGateway) -> None:
        self.session = session
        self.gateway = gateway

    def write_comment(self, task_id: int) -> CommentWritebackResult:
        task = TaskRepository(self.session).get_task(task_id)
        if task is None:
            self.session.rollback()
            raise TaskNotFoundError(f"任务不存在: {task_id}")
        task_status = task.task_status

        try:
            review_result = CurrentReviewResultSelector(self.session).select_for_task(
                task_id
            )
        except CurrentReviewResultNotFoundError as error:
            self.session.rollback()
            if task_status == TaskStatus.REVIEWING:
                TaskStateService(self.session).block_comment_write_precondition(
                    task_id, "REVIEW_RESULT_NOT_FOUND", str(error)
                )
            raise

        successful_log = CommentLogRepository(
            self.session
        ).get_success_for_review_result(review_result.id)
        if successful_log is not None:
            self.session.expunge_all()
            self.session.rollback()
            with self.session.begin():
                TaskRepository(self.session).add_log(
                    task_id,
                    "info",
                    "COMMENT_WRITE_REUSED",
                    (
                        f"复用 ReviewResult {review_result.id} 的成功评论回写，"
                        f"CommentLog: {successful_log.id}"
                    ),
                )
            return CommentWritebackResult(task, successful_log, reused=True)

        if task_status != TaskStatus.REVIEWING:
            current_status = task_status.value
            self.session.rollback()
            raise InvalidTaskStateError(
                f"任务处于 {current_status}，不能执行审批评论回写"
            )

        instance_id = task.instance_id
        review_result_id = review_result.id
        comment_text = review_result.comment_text
        self.session.rollback()
        TaskStateService(self.session).start_comment_write(task_id, review_result_id)
        with self.session.begin():
            attempt = CommentLogRepository(self.session).add(
                CommentLog(
                    task_id=task_id,
                    review_result_id=review_result_id,
                    write_status=WriteStatus.WRITING,
                    write_response_text=None,
                    external_comment_id=None,
                    error_code=None,
                    error_message=None,
                )
            )
        attempt_id = attempt.id

        if not comment_text.strip():
            self._fail_attempt(
                task_id,
                attempt_id,
                "COMMENT_TEXT_EMPTY",
                "当前 ReviewResult 的评论草稿为空",
            )

        try:
            response = self.gateway.write_approval_comment(
                instance_id, review_result_id, comment_text
            )
        except CommentWriteError as error:
            self._fail_attempt(
                task_id, attempt_id, "COMMENT_WRITE_FAILED", str(error)
            )
        except Exception as error:
            self._fail_attempt(
                task_id, attempt_id, "COMMENT_WRITE_FAILED", str(error)
            )

        if not self._valid_response(response):
            self._fail_attempt(
                task_id,
                attempt_id,
                "COMMENT_RESPONSE_INVALID",
                "审批评论接口返回结果缺少成功标识、评论 ID 或消息",
                response_text=str(response),
            )

        with self.session.begin():
            completed_log = CommentLogRepository(self.session).mark_success(
                attempt_id,
                response["message"],
                response["external_comment_id"],
            )
        completed_task = TaskStateService(self.session).complete_comment_write(
            task_id, attempt_id, response["external_comment_id"]
        )
        return CommentWritebackResult(completed_task, completed_log, reused=False)

    def retry_comment_write(self, task_id: int) -> CommentWritebackResult:
        TaskStateService(self.session).resume_comment_writeback(task_id)
        return self.write_comment(task_id)

    def _fail_attempt(
        self,
        task_id: int,
        attempt_id: int,
        error_code: str,
        message: str,
        response_text: str | None = None,
    ) -> NoReturn:
        with self.session.begin():
            CommentLogRepository(self.session).mark_failed(
                attempt_id, error_code, message, response_text
            )
        TaskStateService(self.session).fail_comment_write(
            task_id, attempt_id, error_code, message
        )
        raise CommentWritebackError(f"{error_code}: {message}")

    @staticmethod
    def _valid_response(response: object) -> bool:
        return (
            isinstance(response, dict)
            and response.get("success") is True
            and isinstance(response.get("external_comment_id"), str)
            and bool(response["external_comment_id"].strip())
            and isinstance(response.get("message"), str)
            and bool(response["message"].strip())
        )
