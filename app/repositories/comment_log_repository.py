from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CommentLog, WriteStatus


class CommentLogRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, log: CommentLog) -> CommentLog:
        self.session.add(log)
        self.session.flush()
        return log

    def get_success_for_review_result(
        self, review_result_id: int
    ) -> CommentLog | None:
        statement = (
            select(CommentLog)
            .where(
                CommentLog.review_result_id == review_result_id,
                CommentLog.write_status == WriteStatus.SUCCESS,
            )
            .order_by(CommentLog.id.desc())
        )
        return self.session.scalar(statement)

    def list_for_task(self, task_id: int) -> list[CommentLog]:
        statement = (
            select(CommentLog)
            .where(CommentLog.task_id == task_id)
            .order_by(CommentLog.id)
        )
        return list(self.session.scalars(statement))

    def mark_success(
        self,
        log_id: int,
        response_text: str,
        external_comment_id: str,
    ) -> CommentLog:
        log = self._require(log_id)
        log.write_status = WriteStatus.SUCCESS
        log.write_response_text = response_text
        log.external_comment_id = external_comment_id
        log.error_code = None
        log.error_message = None
        self.session.flush()
        return log

    def mark_failed(
        self,
        log_id: int,
        error_code: str,
        error_message: str,
        response_text: str | None = None,
    ) -> CommentLog:
        log = self._require(log_id)
        log.write_status = WriteStatus.FAILED
        log.error_code = error_code
        log.error_message = error_message
        log.write_response_text = response_text
        self.session.flush()
        return log

    def _require(self, log_id: int) -> CommentLog:
        log = self.session.get(CommentLog, log_id)
        if log is None:
            raise LookupError(f"CommentLog 不存在: {log_id}")
        return log
