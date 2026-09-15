import re
from dataclasses import dataclass
from datetime import date

from app.models import DocumentReadSnapshot
from app.schemas import (
    ContractBasicInfo,
    ContractClauses,
    EvidencePosition,
    ExtractedFact,
    ExtractStatus,
    StructuredContractExtraction,
)


@dataclass(frozen=True)
class ExtractionCandidate:
    value: str
    source_text: str
    position: EvidencePosition


class DeterministicContractExtractor:
    """只处理标签明确、可以解释的合同事实，不进行风险判断。"""

    name = "deterministic_contract_extractor"
    version = "1.0"

    _clause_keywords = {
        "payment_clause": ("付款条款", "付款方式"),
        "delivery_clause": ("交付条款", "交货条款"),
        "acceptance_clause": ("验收条款", "验收标准"),
        "breach_clause": ("违约条款", "违约责任"),
        "confidentiality_clause": ("保密条款", "保密义务"),
        "data_clause": ("数据条款", "数据保护", "数据处理"),
        "intellectual_property_clause": ("知识产权条款", "知识产权"),
        "dispute_resolution_clause": ("争议解决条款", "争议解决"),
    }

    def extract(
        self, document_read: DocumentReadSnapshot
    ) -> StructuredContractExtraction:
        blocks = sorted(
            document_read.blocks_json,
            key=lambda block: int(block["block_index"]),
        )
        basic_info = ContractBasicInfo(
            contract_title=self._extract_title(blocks),
            contract_number=self._extract_regex_field(
                blocks,
                re.compile(r"(?:合同编号|合同号|编号)\s*[：:]\s*([A-Za-z0-9_-]+)"),
                "regex_label",
            ),
            signing_party=self._extract_regex_field(
                blocks, re.compile(r"甲方\s*[：:]\s*(.+)"), "regex_label"
            ),
            counterparty=self._extract_regex_field(
                blocks, re.compile(r"乙方\s*[：:]\s*(.+)"), "regex_label"
            ),
            amount=self._extract_amount(blocks),
            currency=self._extract_currency(blocks),
            effective_date=self._extract_date(blocks, ("生效日期", "有效期自")),
            expiry_date=self._extract_date(blocks, ("到期日期", "有效期至")),
        )
        clauses = self._extract_clauses(blocks)
        return StructuredContractExtraction(basic_info=basic_info, clauses=clauses)

    def _extract_title(self, blocks: list[dict]) -> ExtractedFact:
        candidates = []
        for block in blocks:
            text = str(block["text"]).strip()
            if len(text) <= 50 and re.search(r"(?:合同|协议)$", text):
                candidates.append(self._candidate(text, block))
        return self._fact_from_candidates(candidates, "title_suffix")

    def _extract_regex_field(
        self, blocks: list[dict], pattern: re.Pattern, method: str
    ) -> ExtractedFact:
        candidates = []
        for block in blocks:
            source_text = str(block["text"]).strip()
            match = pattern.search(source_text)
            if match:
                candidates.append(self._candidate(match.group(1).strip(), block))
        return self._fact_from_candidates(candidates, method)

    def _extract_amount(self, blocks: list[dict]) -> ExtractedFact:
        pattern = re.compile(
            r"(?:合同总金额|合同金额|总金额|金额)[^0-9]{0,20}"
            r"([0-9][0-9,]*(?:\.[0-9]+)?)"
        )
        fact = self._extract_regex_field(blocks, pattern, "regex_amount")
        if fact.extract_status == ExtractStatus.FOUND and fact.value:
            fact.value = fact.value.replace(",", "")
        return fact

    def _extract_currency(self, blocks: list[dict]) -> ExtractedFact:
        candidates = []
        currency_keywords = {
            "CNY": ("人民币", "CNY", "RMB", "￥", "¥"),
            "USD": ("美元", "USD", "US$"),
        }
        for block in blocks:
            text = str(block["text"]).strip()
            if not any(label in text for label in ("金额", "价款", "费用")):
                continue
            for currency, keywords in currency_keywords.items():
                if any(keyword.lower() in text.lower() for keyword in keywords):
                    candidates.append(self._candidate(currency, block))
        return self._fact_from_candidates(candidates, "currency_keyword")

    def _extract_date(
        self, blocks: list[dict], labels: tuple[str, ...]
    ) -> ExtractedFact:
        label_pattern = "|".join(re.escape(label) for label in labels)
        pattern = re.compile(
            rf"(?:{label_pattern})\s*[：:]\s*"
            r"(\d{4}(?:年|-|/)\d{1,2}(?:月|-|/)\d{1,2}日?)"
        )
        fact = self._extract_regex_field(blocks, pattern, "regex_date")
        if fact.extract_status == ExtractStatus.FOUND and fact.value:
            try:
                fact.value = self._normalize_date(fact.value)
            except ValueError:
                fact.value = None
                fact.extract_status = ExtractStatus.FAILED
        return fact

    def _extract_clauses(self, blocks: list[dict]) -> ContractClauses:
        headings: list[tuple[int, str]] = []
        for position, block in enumerate(blocks):
            text = str(block["text"])
            for clause_name, keywords in self._clause_keywords.items():
                if any(
                    self._is_clause_heading(text, keyword) for keyword in keywords
                ):
                    headings.append((position, clause_name))
                    break
        headings.sort()

        facts = {}
        for clause_name in self._clause_keywords:
            matches = [item for item in headings if item[1] == clause_name]
            candidates = []
            for heading_position, _ in matches:
                next_positions = [position for position, _ in headings if position > heading_position]
                end_position = min(next_positions) - 1 if next_positions else len(blocks) - 1
                clause_blocks = blocks[heading_position : end_position + 1]
                source_text = "\n".join(str(block["text"]).strip() for block in clause_blocks)
                candidates.append(
                    ExtractionCandidate(
                        value=source_text,
                        source_text=source_text,
                        position=self._position_for_blocks(clause_blocks),
                    )
                )
            facts[clause_name] = self._fact_from_candidates(
                candidates, "heading_keyword"
            )
        return ContractClauses(**facts)

    @staticmethod
    def _candidate(value: str, block: dict) -> ExtractionCandidate:
        source_text = str(block["text"]).strip()
        return ExtractionCandidate(
            value=value,
            source_text=source_text,
            position=EvidencePosition(
                block_start=int(block["block_index"]),
                block_end=int(block["block_index"]),
                page_start=block.get("page_number"),
                page_end=block.get("page_number"),
            ),
        )

    @staticmethod
    def _fact_from_candidates(
        candidates: list[ExtractionCandidate], method: str
    ) -> ExtractedFact:
        if not candidates:
            return ExtractedFact(
                value=None,
                source_text=None,
                position=None,
                extract_status=ExtractStatus.NOT_FOUND,
                extract_method=method,
            )
        distinct_values = {candidate.value for candidate in candidates}
        if len(distinct_values) > 1:
            return ExtractedFact(
                value=None,
                source_text="\n".join(candidate.source_text for candidate in candidates),
                position=EvidencePosition(
                    block_start=min(candidate.position.block_start for candidate in candidates),
                    block_end=max(candidate.position.block_end for candidate in candidates),
                    page_start=DeterministicContractExtractor._first_page(candidates),
                    page_end=DeterministicContractExtractor._last_page(candidates),
                ),
                extract_status=ExtractStatus.AMBIGUOUS,
                extract_method=method,
            )
        candidate = candidates[0]
        return ExtractedFact(
            value=candidate.value,
            source_text=candidate.source_text,
            position=candidate.position,
            extract_status=ExtractStatus.FOUND,
            extract_method=method,
        )

    @staticmethod
    def _position_for_blocks(blocks: list[dict]) -> EvidencePosition:
        pages = [
            block.get("page_number")
            for block in blocks
            if block.get("page_number") is not None
        ]
        return EvidencePosition(
            block_start=int(blocks[0]["block_index"]),
            block_end=int(blocks[-1]["block_index"]),
            page_start=int(pages[0]) if pages else None,
            page_end=int(pages[-1]) if pages else None,
        )

    @staticmethod
    def _first_page(candidates: list[ExtractionCandidate]) -> int | None:
        pages = [candidate.position.page_start for candidate in candidates if candidate.position.page_start is not None]
        return min(pages) if pages else None

    @staticmethod
    def _last_page(candidates: list[ExtractionCandidate]) -> int | None:
        pages = [candidate.position.page_end for candidate in candidates if candidate.position.page_end is not None]
        return max(pages) if pages else None

    @staticmethod
    def _normalize_date(value: str) -> str:
        parts = [int(part) for part in re.findall(r"\d+", value)]
        return date(parts[0], parts[1], parts[2]).isoformat()

    @staticmethod
    def _is_clause_heading(text: str, keyword: str) -> bool:
        clean_text = text.strip()
        return clean_text.startswith(keyword) or bool(
            re.match(r"^第[^\s]{1,8}条", clean_text) and keyword in clean_text
        )


def failed_contract_extraction() -> StructuredContractExtraction:
    failed_fact = ExtractedFact(
        value=None,
        source_text=None,
        position=None,
        extract_status=ExtractStatus.FAILED,
        extract_method="system_error",
    )
    return StructuredContractExtraction(
        basic_info=ContractBasicInfo(
            **{
                field_name: failed_fact.model_copy(deep=True)
                for field_name in ContractBasicInfo.model_fields
            }
        ),
        clauses=ContractClauses(
            **{
                field_name: failed_fact.model_copy(deep=True)
                for field_name in ContractClauses.model_fields
            }
        ),
    )
