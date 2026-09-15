from dataclasses import dataclass
from typing import Protocol

from app.models import ContractParse, EvidenceType, ReviewRule


@dataclass(frozen=True)
class RuleMatch:
    rule_id: int
    rule_version: str
    evidence_text: str
    evidence_position: dict | None
    evidence_type: EvidenceType
    actual_value: str | None
    expected_value: str | None
    hit_message: str


class RuleEngine(Protocol):
    name: str
    version: str

    def run(
        self, contract_parse: ContractParse, active_rules: list[ReviewRule]
    ) -> list[RuleMatch]: ...
