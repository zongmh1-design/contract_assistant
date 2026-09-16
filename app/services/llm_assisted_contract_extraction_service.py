from __future__ import annotations

from hashlib import sha256
import json

from sqlalchemy.orm import Session

from app.integrations.llm import LLMProvider, LlmProviderError
from app.models import ContractParse, ContractParseStatus
from app.parsers import (
    DeterministicContractExtractor,
    LlmAssistedExtraction,
    LlmContractExtractor,
)
from app.repositories import ContractParseRepository, TaskRepository
from app.schemas import StructuredContractExtraction
from app.services.contract_extraction_service import ContractExtractionService
from app.services.document_source_selector import DocumentSourceSelector


class LlmProviderNotConfiguredError(RuntimeError):
    pass


class LlmAssistedContractExtractionService:
    """保留确定性解析，并把辅助结果保存为新的 hybrid ContractParse。"""

    def __init__(self, session: Session, provider: LLMProvider) -> None:
        self.session = session
        self.provider = provider
        self.deterministic_extractor = DeterministicContractExtractor()
        self.llm_extractor = LlmContractExtractor(provider)

    def parse_contract(self, task_id: int) -> ContractParse:
        deterministic_parse = ContractExtractionService(
            self.session, self.deterministic_extractor
        ).parse_contract(task_id)
        if deterministic_parse.parse_status == ContractParseStatus.FAILED:
            return deterministic_parse

        document_read = DocumentSourceSelector(self.session).select_for_task(task_id)
        deterministic = StructuredContractExtraction(
            basic_info=deterministic_parse.basic_info_json,
            clauses=deterministic_parse.clause_info_json,
        )
        extractor_version = self._hybrid_version()
        reusable = ContractParseRepository(self.session).find_reusable(
            document_read.id,
            self.llm_extractor.name,
            extractor_version,
        )
        if reusable is not None and (reusable.llm_metadata_json or {}).get(
            "status"
        ) != "degraded":
            reusable_id = reusable.id
            self.session.expunge(reusable)
            self.session.rollback()
            with self.session.begin():
                TaskRepository(self.session).add_log(
                    task_id,
                    "info",
                    "LLM_CONTRACT_PARSE_REUSED",
                    f"复用 LLM 辅助合同解析 {reusable_id}",
                )
            return reusable

        self.session.expunge(document_read)
        self.session.rollback()
        try:
            outcome = self.llm_extractor.enhance(document_read, deterministic)
        except LlmProviderError as error:
            outcome = self._degraded_outcome(deterministic, error)
        except Exception as error:
            outcome = self._degraded_outcome(deterministic, error)

        parse_status = ContractExtractionService.status_for(outcome.extraction)
        if outcome.metadata.get("degraded"):
            parse_status = ContractParseStatus.PARTIAL
        log_type = self._log_type(outcome.metadata)
        with self.session.begin():
            contract_parse = ContractParseRepository(self.session).add(
                ContractParse(
                    document_read_snapshot_id=document_read.id,
                    basic_info_json=outcome.extraction.basic_info.model_dump(mode="json"),
                    clause_info_json=outcome.extraction.clauses.model_dump(mode="json"),
                    parse_status=parse_status,
                    parse_error=None,
                    extractor_name=self.llm_extractor.name,
                    extractor_version=extractor_version,
                    llm_metadata_json=outcome.metadata,
                )
            )
            TaskRepository(self.session).add_log(
                task_id,
                "warning" if outcome.metadata["status"] == "degraded" else "info",
                log_type,
                self._log_content(contract_parse.id, outcome.metadata),
            )
            if outcome.metadata["conflicts"]:
                TaskRepository(self.session).add_log(
                    task_id,
                    "warning",
                    "LLM_EXTRACTION_CONFLICT",
                    "LLM 返回了确定性 found 字段的不同值，已保留确定性结果："
                    + json.dumps(outcome.metadata["conflicts"], ensure_ascii=False),
                )
        return contract_parse

    def _hybrid_version(self) -> str:
        source = (
            f"{self.llm_extractor.version}|{self.deterministic_extractor.version}|"
            f"{self.provider.provider_name}|{self.provider.model_name}"
        )
        return f"{self.llm_extractor.version}-{sha256(source.encode()).hexdigest()[:12]}"

    def _degraded_outcome(
        self, deterministic: StructuredContractExtraction, error: Exception
    ) -> LlmAssistedExtraction:
        return LlmAssistedExtraction(
            extraction=deterministic.model_copy(deep=True),
            metadata={
                "status": "degraded",
                "degraded": True,
                "provider": self.provider.provider_name,
                "model": self.provider.model_name,
                "llm_extractor_version": self.llm_extractor.version,
                "requested_fields": self.llm_extractor.unresolved_fields(deterministic),
                "resolved_fields": [],
                "conflicts": [],
                "validation_errors": [],
                "token_usage": {
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "total_tokens": None,
                },
                "error_type": type(error).__name__,
                "error_message": str(error),
            },
        )

    @staticmethod
    def _log_type(metadata: dict) -> str:
        if metadata["status"] == "degraded":
            return "LLM_EXTRACTION_DEGRADED"
        if metadata["status"] == "no_unresolved_fields":
            return "LLM_EXTRACTION_SKIPPED"
        return "LLM_EXTRACTION_COMPLETED"

    @staticmethod
    def _log_content(contract_parse_id: int, metadata: dict) -> str:
        if metadata["status"] == "degraded":
            return (
                f"LLM 辅助失败并降级为确定性结果，ContractParse: {contract_parse_id}，"
                f"错误: {metadata['error_type']}: {metadata['error_message']}"
            )
        return (
            f"LLM 辅助提取完成，ContractParse: {contract_parse_id}，"
            f"请求 {len(metadata['requested_fields'])} 项，"
            f"补充 {len(metadata['resolved_fields'])} 项"
        )
