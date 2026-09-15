from copy import deepcopy
from datetime import datetime, timezone

from app.integrations.approval.gateway import (
    ApprovalDetailData,
    AttachmentDownloadError,
    PendingApprovalData,
    CommentWriteError,
    CommentWriteResponse,
)


class MockApprovalNotFoundError(LookupError):
    pass


class MockAttachmentDownloadError(AttachmentDownloadError):
    pass


class MockApprovalGateway:
    """固定数据适配器，覆盖正常、重复及附件异常场景。"""

    def __init__(self) -> None:
        self._pending_items: list[PendingApprovalData] = [
            {
                "instance_id": "MOCK-INSTANCE-001",
                "approval_code": "APPROVAL-2026-001",
                "approval_title": "采购合同审批",
                "applicant_name": "张敏",
                "applied_at": datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc),
            },
            {
                "instance_id": "MOCK-INSTANCE-002",
                "approval_code": "APPROVAL-2026-002",
                "approval_title": "软件服务合同审批",
                "applicant_name": "李强",
                "applied_at": datetime(2026, 9, 15, 9, 30, tzinfo=timezone.utc),
            },
            {
                "instance_id": "MOCK-INSTANCE-FAIL-001",
                "approval_code": "APPROVAL-2026-FAIL-001",
                "approval_title": "扫描件合同审批（失败样例）",
                "applicant_name": "王芳",
                "applied_at": datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc),
            },
            {
                "instance_id": "MOCK-INSTANCE-001",
                "approval_code": "APPROVAL-2026-001",
                "approval_title": "采购合同审批（信息已更新）",
                "applicant_name": "张敏",
                "applied_at": datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc),
            },
            {
                "instance_id": "MOCK-NO-ATTACHMENT",
                "approval_code": "APPROVAL-2026-NO-ATTACHMENT",
                "approval_title": "无附件审批",
                "applicant_name": "陈静",
                "applied_at": datetime(2026, 9, 15, 10, 30, tzinfo=timezone.utc),
            },
            {
                "instance_id": "MOCK-NO-MAIN-CONTRACT",
                "approval_code": "APPROVAL-2026-NO-MAIN",
                "approval_title": "无法识别主合同审批",
                "applicant_name": "赵磊",
                "applied_at": datetime(2026, 9, 15, 11, 0, tzinfo=timezone.utc),
            },
            {
                "instance_id": "MOCK-UNSUPPORTED-TYPE",
                "approval_code": "APPROVAL-2026-UNSUPPORTED",
                "approval_title": "不支持格式审批",
                "applicant_name": "孙悦",
                "applied_at": datetime(2026, 9, 15, 11, 30, tzinfo=timezone.utc),
            },
            {
                "instance_id": "MOCK-DOWNLOAD-FAIL",
                "approval_code": "APPROVAL-2026-DOWNLOAD-FAIL",
                "approval_title": "下载失败审批",
                "applicant_name": "周航",
                "applied_at": datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
            },
        ]

        self._details: dict[str, ApprovalDetailData] = {
            "MOCK-INSTANCE-001": {
                **self._pending_items[0],
                "form_data": {"department": "采购部"},
                "attachments": [
                    {
                        "attachment_id": "att_quotation_001",
                        "file_name": "报价单.pdf",
                        "file_type": "pdf",
                        "is_main_contract": False,
                    },
                    {
                        "attachment_id": "att_001",
                        "file_name": "采购合同.pdf",
                        "file_type": "pdf",
                        "is_main_contract": True,
                    },
                ],
                "simulate_parsing_failure": False,
                "failure_reason": None,
            },
            "MOCK-INSTANCE-002": {
                **self._pending_items[1],
                "form_data": {"department": "信息技术部"},
                "attachments": [
                    {
                        "attachment_id": "att_002",
                        "file_name": "Service Agreement.DOCX",
                        "file_type": "docx",
                        "is_main_contract": False,
                    }
                ],
                "simulate_parsing_failure": False,
                "failure_reason": None,
            },
            "MOCK-INSTANCE-FAIL-001": {
                **self._pending_items[2],
                "form_data": {"department": "行政部"},
                "attachments": [
                    {
                        "attachment_id": "att_old_failure_001",
                        "file_name": "扫描件合同.pdf",
                        "file_type": "pdf",
                        "is_main_contract": True,
                    }
                ],
                "simulate_parsing_failure": True,
                "failure_reason": "模拟文档内容为空，无法继续解析",
            },
            "MOCK-NO-ATTACHMENT": {
                **self._pending_items[4],
                "form_data": {"department": "财务部"},
                "attachments": [],
                "simulate_parsing_failure": False,
                "failure_reason": None,
            },
            "MOCK-NO-MAIN-CONTRACT": {
                **self._pending_items[5],
                "form_data": {"department": "采购部"},
                "attachments": [
                    {
                        "attachment_id": "att_quote_001",
                        "file_name": "供应商报价单.pdf",
                        "file_type": "pdf",
                        "is_main_contract": False,
                    }
                ],
                "simulate_parsing_failure": False,
                "failure_reason": None,
            },
            "MOCK-UNSUPPORTED-TYPE": {
                **self._pending_items[6],
                "form_data": {"department": "法务部"},
                "attachments": [
                    {
                        "attachment_id": "att_doc_001",
                        "file_name": "历史合同.doc",
                        "file_type": "doc",
                        "is_main_contract": True,
                    }
                ],
                "simulate_parsing_failure": False,
                "failure_reason": None,
            },
            "MOCK-DOWNLOAD-FAIL": {
                **self._pending_items[7],
                "form_data": {"department": "销售部"},
                "attachments": [
                    {
                        "attachment_id": "att_download_fail_001",
                        "file_name": "销售合同.pdf",
                        "file_type": "pdf",
                        "is_main_contract": True,
                    }
                ],
                "simulate_parsing_failure": False,
                "failure_reason": None,
            },
        }

        self._attachment_content: dict[tuple[str, str], bytes] = {
            ("MOCK-INSTANCE-001", "att_001"): b"mock pdf contract version 1",
            ("MOCK-INSTANCE-001", "att_quotation_001"): b"mock quotation",
            ("MOCK-INSTANCE-002", "att_002"): b"mock docx agreement",
            ("MOCK-INSTANCE-FAIL-001", "att_old_failure_001"): b"mock scan pdf",
            ("MOCK-DOWNLOAD-FAIL", "att_download_fail_001"): b"download succeeds on retry",
        }
        self._remaining_download_failures: dict[tuple[str, str], int] = {
            ("MOCK-DOWNLOAD-FAIL", "att_download_fail_001"): 1
        }
        self._comment_write_modes: dict[str, str] = {}
        self.attachment_download_call_count = 0
        self.comment_write_call_count = 0
        self.created_comment_count = 0
        self.written_comments: dict[tuple[str, int], CommentWriteResponse] = {}

    def list_pending_contract_approvals(self, limit: int) -> list[PendingApprovalData]:
        if limit < 1:
            return []
        return deepcopy(self._pending_items[:limit])

    def get_contract_approval(self, instance_id: str) -> ApprovalDetailData:
        detail = self._details.get(instance_id)
        if detail is None:
            raise MockApprovalNotFoundError(f"Mock 审批不存在: {instance_id}")
        return deepcopy(detail)

    def download_contract_attachment(
        self, instance_id: str, attachment_id: str, file_name: str
    ) -> bytes:
        self.attachment_download_call_count += 1
        key = (instance_id, attachment_id)
        remaining_failures = self._remaining_download_failures.get(key, 0)
        if remaining_failures > 0:
            self._remaining_download_failures[key] = remaining_failures - 1
            raise MockAttachmentDownloadError(
                f"模拟附件下载失败: {attachment_id} ({file_name})"
            )

        content = self._attachment_content.get(key)
        if content is None:
            raise MockAttachmentDownloadError(f"Mock 附件内容不存在: {attachment_id}")
        return bytes(content)

    def set_attachment_content(
        self, instance_id: str, attachment_id: str, content: bytes
    ) -> None:
        """仅供测试模拟上游附件内容发生变化。"""
        self._attachment_content[(instance_id, attachment_id)] = bytes(content)

    def set_attachment_file_name(
        self, instance_id: str, attachment_id: str, file_name: str
    ) -> None:
        """仅供测试路径安全处理。"""
        detail = self._details[instance_id]
        attachment = next(
            item
            for item in detail["attachments"]
            if item["attachment_id"] == attachment_id
        )
        attachment["file_name"] = file_name

    def write_approval_comment(
        self, instance_id: str, review_id: int, comment_text: str
    ) -> CommentWriteResponse:
        self.comment_write_call_count += 1
        key = (instance_id, review_id)
        existing = self.written_comments.get(key)
        if existing is not None:
            return deepcopy(existing)

        mode = self._comment_write_modes.get(instance_id, "success")
        if mode == "failure":
            raise CommentWriteError(f"模拟审批评论接口失败: {instance_id}")
        if mode == "invalid":
            return {
                "success": True,
                "external_comment_id": "",
                "message": "",
            }
        response: CommentWriteResponse = {
            "success": True,
            "external_comment_id": f"mock-comment-{review_id}",
            "message": "Mock 审批评论写入成功",
        }
        self.written_comments[key] = response
        self.created_comment_count += 1
        return deepcopy(response)

    def set_comment_write_mode(self, instance_id: str, mode: str) -> None:
        """仅供测试切换 success、failure、invalid 场景。"""

        if mode not in {"success", "failure", "invalid"}:
            raise ValueError(f"不支持的 Mock 评论模式: {mode}")
        self._comment_write_modes[instance_id] = mode
