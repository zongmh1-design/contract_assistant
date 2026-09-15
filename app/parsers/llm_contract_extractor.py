from __future__ import annotations

from dataclasses import dataclass
import json
import re

from app.integrations.llm import LLMProvider
from app.models import DocumentReadSnapshot
from app.schemas import (
    ContractBasicInfo,
    ContractClauses,
    EvidencePosition,
    ExtractedFact,
    ExtractStatus,
    LlmExtractionResponse,
    LlmExtractStatus,
    StructuredContractExtraction,
)


BASIC_FIELD_NAMES = tuple(ContractBasicInfo.model_fields)
CLAUSE_FIELD_NAMES = tuple(ContractClauses.model_fields)
SUPPORTED_FIELD_NAMES = frozenset(BASIC_FIELD_NAMES + CLAUSE_FIELD_NAMES)

SYSTEM_PROMPT = """你是合同信息抽取组件，不是合同审查员。
只抽取请求中列出的字段或条款，不判断风险、公平性，不给法律建议或审批意见。
value 必须是证据块中的原文值；found 必须返回真实 block_start 和 block_end。
无法由原文明确支持时返回 not_found 或 ambiguous，不得编造页码或证据。"""


@dataclass(frozen=True)
class LlmAssistedExtraction:
    extraction: StructuredContractExtraction
    metadata: dict


class LlmContractExtractor:
    name = "hybrid_contract_extractor"
    version = "1.0"

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def enhance(
        self,
        document_read: DocumentReadSnapshot,
        deterministic: StructuredContractExtraction,
    ) -> LlmAssistedExtraction:
        merged = deterministic.model_copy(deep=True)
        unresolved = self.unresolved_fields(deterministic)
        metadata = self._base_metadata(unresolved)
        if not unresolved:
            metadata["status"] = "no_unresolved_fields"
            return LlmAssistedExtraction(merged, metadata)

        generation = self.provider.generate_structured(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=self._user_prompt(document_read, deterministic, unresolved),
            response_schema=LlmExtractionResponse,
        )
        metadata.update(
            {
                "status": "success",
                "provider": generation.provider,
                "model": generation.model,
                "token_usage": {
                    "prompt_tokens": generation.usage.prompt_tokens,
                    "completion_tokens": generation.usage.completion_tokens,
                    "total_tokens": generation.usage.total_tokens,
                },
            }
        )
        self._merge_candidates(
            document_read,
            deterministic,
            merged,
            unresolved,
            generation.output,
            metadata,
        )
        return LlmAssistedExtraction(merged, metadata)

    def _merge_candidates(
        self,
        document_read: DocumentReadSnapshot,
        deterministic: StructuredContractExtraction,
        merged: StructuredContractExtraction,
        unresolved: list[str],
        response: LlmExtractionResponse,
        metadata: dict,
    ) -> None:
        requested = set(unresolved)
        seen: set[str] = set()
        for candidate in response.items:
            field_name = candidate.field_name
            if field_name not in SUPPORTED_FIELD_NAMES:
                metadata["validation_errors"].append(
                    {"field_name": field_name, "reason": "UNKNOWN_FIELD"}
                )
                continue
            deterministic_fact = self._get_fact(deterministic, field_name)
            if field_name not in requested:
                if (
                    deterministic_fact.extract_status == ExtractStatus.FOUND
                    and candidate.value != deterministic_fact.value
                ):
                    metadata["conflicts"].append(
                        {
                            "field_name": field_name,
                            "deterministic_value": deterministic_fact.value,
                            "llm_value": candidate.value,
                        }
                    )
                continue
            if field_name in seen:
                metadata["validation_errors"].append(
                    {"field_name": field_name, "reason": "DUPLICATE_RESULT"}
                )
                continue
            seen.add(field_name)

            if candidate.extract_status == LlmExtractStatus.NOT_FOUND:
                continue
            evidence = self._rebuild_evidence(document_read, candidate)
            if evidence is None:
                self._set_fact(
                    merged,
                    field_name,
                    ExtractedFact(
                        value=None,
                        source_text=None,
                        position=None,
                        extract_status=ExtractStatus.FAILED,
                        extract_method="llm_evidence_validation",
                    ),
                )
                metadata["validation_errors"].append(
                    {"field_name": field_name, "reason": "INVALID_BLOCK_RANGE"}
                )
                continue
            source_text, position = evidence
            if candidate.extract_status == LlmExtractStatus.AMBIGUOUS:
                self._set_fact(
                    merged,
                    field_name,
                    ExtractedFact(
                        value=None,
                        source_text=source_text,
                        position=position,
                        extract_status=ExtractStatus.AMBIGUOUS,
                        extract_method="llm_verified",
                    ),
                )
                continue
            if not self._source_supports_value(source_text, candidate.value or ""):
                self._set_fact(
                    merged,
                    field_name,
                    ExtractedFact(
                        value=None,
                        source_text=source_text,
                        position=position,
                        extract_status=ExtractStatus.AMBIGUOUS,
                        extract_method="llm_evidence_validation",
                    ),
                )
                metadata["validation_errors"].append(
                    {"field_name": field_name, "reason": "VALUE_NOT_IN_EVIDENCE"}
                )
                continue
            self._set_fact(
                merged,
                field_name,
                ExtractedFact(
                    value=candidate.value,
                    source_text=source_text,
                    position=position,
                    extract_status=ExtractStatus.FOUND,
                    extract_method="llm_verified",
                ),
            )
            metadata["resolved_fields"].append(field_name)

    def _base_metadata(self, unresolved: list[str]) -> dict:
        return {
            "status": "pending",
            "provider": self.provider.provider_name,
            "model": self.provider.model_name,
            "llm_extractor_version": self.version,
            "requested_fields": unresolved,
            "resolved_fields": [],
            "conflicts": [],
            "validation_errors": [],
            "token_usage": {
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
            },
        }

    @staticmethod
    def unresolved_fields(extraction: StructuredContractExtraction) -> list[str]:
        return [
            field_name
            for field_name in BASIC_FIELD_NAMES + CLAUSE_FIELD_NAMES
            if LlmContractExtractor._get_fact(extraction, field_name).extract_status
            in {ExtractStatus.NOT_FOUND, ExtractStatus.AMBIGUOUS}
        ]

    @staticmethod
    def _user_prompt(
        document_read: DocumentReadSnapshot,
        deterministic: StructuredContractExtraction,
        unresolved: list[str],
    ) -> str:
        blocks = [
            {
                "block_index": block["block_index"],
                "page_number": block.get("page_number"),
                "text": block["text"],
            }
            for block in sorted(
                document_read.blocks_json, key=lambda item: int(item["block_index"])
            )
        ]
        deterministic_context = {
            field_name: {
                "value": LlmContractExtractor._get_fact(
                    deterministic, field_name
                ).value,
                "extract_status": LlmContractExtractor._get_fact(
                    deterministic, field_name
                ).extract_status.value,
            }
            for field_name in BASIC_FIELD_NAMES + CLAUSE_FIELD_NAMES
        }
        return json.dumps(
            {
                "target_fields": unresolved,
                "deterministic_extraction": deterministic_context,
                "document_blocks": blocks,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _rebuild_evidence(document_read, candidate) -> tuple[str, EvidencePosition] | None:
        start = candidate.block_start
        end = candidate.block_end
        if start is None or end is None or start > end:
            return None
        block_map = {
            int(block["block_index"]): block for block in document_read.blocks_json
        }
        indexes = list(range(start, end + 1))
        if any(index not in block_map for index in indexes):
            return None
        blocks = [block_map[index] for index in indexes]
        pages = [block.get("page_number") for block in blocks]
        real_pages = [int(page) for page in pages if page is not None]
        return (
            "\n".join(str(block["text"]).strip() for block in blocks),
            EvidencePosition(
                block_start=start,
                block_end=end,
                page_start=real_pages[0] if real_pages else None,
                page_end=real_pages[-1] if real_pages else None,
            ),
        )

    @staticmethod
    def _source_supports_value(source_text: str, value: str) -> bool:
        def normalize(text: str) -> str:
            return re.sub(r"[\s,，。；;：:（）()￥¥$\-年月日/]", "", text).casefold()

        normalized_value = normalize(value)
        return bool(normalized_value) and normalized_value in normalize(source_text)

    @staticmethod
    def _get_fact(
        extraction: StructuredContractExtraction, field_name: str
    ) -> ExtractedFact:
        container = (
            extraction.basic_info
            if field_name in BASIC_FIELD_NAMES
            else extraction.clauses
        )
        return getattr(container, field_name)

    @staticmethod
    def _set_fact(
        extraction: StructuredContractExtraction,
        field_name: str,
        fact: ExtractedFact,
    ) -> None:
        container = (
            extraction.basic_info
            if field_name in BASIC_FIELD_NAMES
            else extraction.clauses
        )
        setattr(container, field_name, fact)
