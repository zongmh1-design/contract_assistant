from datetime import datetime
from typing import Protocol, TypedDict


class PendingApprovalData(TypedDict):
    instance_id: str
    approval_code: str
    approval_title: str
    applicant_name: str
    applied_at: datetime


class ApprovalAttachmentData(TypedDict):
    attachment_id: str
    file_name: str
    file_type: str
    is_main_contract: bool


class ApprovalDetailData(PendingApprovalData):
    form_data: dict[str, object]
    attachments: list[ApprovalAttachmentData]
    simulate_parsing_failure: bool
    failure_reason: str | None


class AttachmentDownloadError(IOError):
    """审批适配器下载附件失败时统一抛出的边界异常。"""


class CommentWriteResponse(TypedDict):
    success: bool
    external_comment_id: str
    message: str


class CommentWriteError(IOError):
    """审批适配器写入评论失败时统一抛出的边界异常。"""


class ApprovalGateway(Protocol):
    def list_pending_contract_approvals(self, limit: int) -> list[PendingApprovalData]: ...

    def get_contract_approval(self, instance_id: str) -> ApprovalDetailData: ...

    def download_contract_attachment(
        self, instance_id: str, attachment_id: str, file_name: str
    ) -> bytes: ...

    def write_approval_comment(
        self, instance_id: str, review_id: int, comment_text: str
    ) -> CommentWriteResponse: ...
