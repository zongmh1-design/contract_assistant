from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from app.integrations.llm import LLMProvider
from app.models import ContractParse, DocumentReadSnapshot, ReviewRule
from app.schemas import LlmRuleDecision, LlmSemanticRuleResponse


SYSTEM_PROMPT = """你是合同语义风险线索识别组件，不是法律裁决者。
你只能根据提供的合同原文块判断指定规则，输出 hit、not_hit 或 uncertain。
不得判断合同是否合法、是否应批准，不得给出风险等级或处理建议。
hit 必须引用输入中真实且连续的 block_start/block_end；无法确认时返回 uncertain。"""


@dataclass(frozen=True)
class SemanticRuleOutcome:
    decision: LlmRuleDecision
    reason: str
    evidence_text: str | None
    evidence_position: dict | None
    evidence_valid: bool
    provider: str
    model: str
    token_usage: dict


class LlmSemanticRuleEngine:
    """只判断单条语义规则，证据文本和页码始终由系统从 blocks 重建。"""

    version = "1.0"

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def evaluation_fingerprint(self, rule: ReviewRule) -> str:
        payload = "|".join(
            (
                rule.rule_version,
                self.version,
                self.provider.provider_name,
                self.provider.model_name,
            )
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    def evaluate(
        self,
        contract_parse: ContractParse,
        document_read: DocumentReadSnapshot,
        rule: ReviewRule,
    ) -> SemanticRuleOutcome:
        config = json.loads(rule.match_text)
        target_clause = str(config["target_clause"])
        clause = contract_parse.clause_info_json[target_clause]
        allowed_blocks = self._target_blocks(document_read, clause)
        if not allowed_blocks:
            raise ValueError(f"目标条款没有可验证的原文块: {target_clause}")

        generation = self.provider.generate_structured(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=json.dumps(
                {
                    "rule_code": rule.rule_code,
                    "instruction": config["instruction"],
                    "severity_policy": config.get("severity_policy"),
                    "target_clause": target_clause,
                    "document_blocks": allowed_blocks,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            response_schema=LlmSemanticRuleResponse,
        )
        response = generation.output
        evidence = self._rebuild_evidence(
            allowed_blocks, response.block_start, response.block_end
        )
        evidence_valid = response.decision != LlmRuleDecision.HIT or evidence is not None
        evidence_text, evidence_position = evidence or (None, None)
        return SemanticRuleOutcome(
            decision=response.decision,
            reason=response.reason,
            evidence_text=evidence_text,
            evidence_position=evidence_position,
            evidence_valid=evidence_valid,
            provider=generation.provider,
            model=generation.model,
            token_usage={
                "prompt_tokens": generation.usage.prompt_tokens,
                "completion_tokens": generation.usage.completion_tokens,
                "total_tokens": generation.usage.total_tokens,
            },
        )

    @staticmethod
    def clause_is_available(contract_parse: ContractParse, rule: ReviewRule) -> bool:
        try:
            target = json.loads(rule.match_text)["target_clause"]
            clause = contract_parse.clause_info_json[target]
        except (KeyError, TypeError, json.JSONDecodeError):
            return False
        return clause.get("extract_status") == "found" and bool(clause.get("position"))

    @staticmethod
    def _target_blocks(document_read: DocumentReadSnapshot, clause: dict) -> list[dict]:
        position = clause.get("position") or {}
        start = position.get("block_start")
        end = position.get("block_end")
        if not isinstance(start, int) or not isinstance(end, int) or start > end:
            return []
        return [
            {
                "block_index": int(block["block_index"]),
                "page_number": block.get("page_number"),
                "text": block["text"],
            }
            for block in sorted(
                document_read.blocks_json, key=lambda item: int(item["block_index"])
            )
            if start <= int(block["block_index"]) <= end
        ]

    @staticmethod
    def _rebuild_evidence(
        allowed_blocks: list[dict], start: int | None, end: int | None
    ) -> tuple[str, dict] | None:
        if start is None or end is None or start > end:
            return None
        block_map = {int(block["block_index"]): block for block in allowed_blocks}
        indexes = list(range(start, end + 1))
        if any(index not in block_map for index in indexes):
            return None
        blocks = [block_map[index] for index in indexes]
        pages = [int(block["page_number"]) for block in blocks if block.get("page_number") is not None]
        return (
            "\n".join(str(block["text"]).strip() for block in blocks),
            {
                "block_start": start,
                "block_end": end,
                "page_start": pages[0] if pages else None,
                "page_end": pages[-1] if pages else None,
            },
        )
