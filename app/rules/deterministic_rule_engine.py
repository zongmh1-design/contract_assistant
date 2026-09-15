import json
import re

from app.models import ContractParse, EvidenceType, MatchMode, ReviewRule
from app.rules.rule_engine import RuleMatch


class DeterministicRuleEngine:
    """按规则配置执行可解释的缺失、阈值、关键词和存在性判断。"""

    name = "deterministic_rule_engine"
    version = "1.0"

    def run(
        self, contract_parse: ContractParse, active_rules: list[ReviewRule]
    ) -> list[RuleMatch]:
        document = {
            "basic_info": contract_parse.basic_info_json,
            "clauses": contract_parse.clause_info_json,
        }
        matches: list[RuleMatch] = []
        for rule in active_rules:
            if rule.match_mode == MatchMode.FUTURE_LLM:
                continue
            config = json.loads(rule.match_text)
            match = self._evaluate(rule, config, document)
            if match is not None:
                matches.append(match)
        return matches

    def _evaluate(
        self, rule: ReviewRule, config: dict, document: dict
    ) -> RuleMatch | None:
        if rule.match_mode == MatchMode.FIELD_MISSING:
            return self._field_missing(rule, config, document)
        if rule.match_mode == MatchMode.NUMERIC_THRESHOLD:
            return self._numeric_threshold(rule, config, document)
        if rule.match_mode in {MatchMode.KEYWORD, MatchMode.PRESENCE}:
            return self._keyword(rule, config, document)
        raise ValueError(f"不支持的确定性匹配模式: {rule.match_mode.value}")

    def _field_missing(
        self, rule: ReviewRule, config: dict, document: dict
    ) -> RuleMatch | None:
        missing_paths = [
            path
            for path in config["fields"]
            if self._is_missing(self._fact(document, path))
        ]
        trigger = config.get("trigger", "any")
        is_hit = bool(missing_paths) if trigger == "any" else len(missing_paths) == len(config["fields"])
        if not is_hit:
            return None
        message = config["hit_message"]
        return self._match(
            rule,
            evidence_text=message,
            evidence_position=None,
            evidence_type=EvidenceType.MISSING,
            actual_value=", ".join(missing_paths),
            expected_value="字段应可明确提取",
            hit_message=message,
        )

    def _numeric_threshold(
        self, rule: ReviewRule, config: dict, document: dict
    ) -> RuleMatch | None:
        fact = self._fact(document, config["field"])
        if self._is_missing(fact):
            return None
        source_text = fact.get("source_text") or fact.get("value") or ""
        numbers = self._metric_values(config["metric"], source_text)
        if not numbers:
            return None
        actual = max(numbers)
        threshold = float(config["threshold"])
        if config.get("operator") != "gt":
            raise ValueError("第一版 numeric_threshold 只支持 gt")
        if actual <= threshold:
            return None
        unit = config.get("unit", "")
        suffix = "%" if unit == "percent" else "天" if unit == "days" else ""
        return self._match(
            rule,
            evidence_text=source_text,
            evidence_position=fact.get("position"),
            evidence_type=EvidenceType.DERIVED,
            actual_value=f"{self._format_number(actual)}{suffix}",
            expected_value=f"不高于 {self._format_number(threshold)}{suffix}",
            hit_message=config["hit_message"],
        )

    def _keyword(
        self, rule: ReviewRule, config: dict, document: dict
    ) -> RuleMatch | None:
        keywords = config["keywords"]
        for path in config["fields"]:
            fact = self._fact(document, path)
            if self._is_missing(fact):
                continue
            source_text = fact.get("source_text") or fact.get("value") or ""
            keyword = next((item for item in keywords if item in source_text), None)
            if keyword is None:
                continue
            return self._match(
                rule,
                evidence_text=source_text,
                evidence_position=fact.get("position"),
                evidence_type=EvidenceType.SOURCE,
                actual_value=keyword,
                expected_value=None,
                hit_message=config["hit_message"],
            )
        return None

    @staticmethod
    def _fact(document: dict, path: str) -> dict:
        value: object = document
        for segment in path.split("."):
            if not isinstance(value, dict) or segment not in value:
                raise ValueError(f"规则字段路径不存在: {path}")
            value = value[segment]
        if not isinstance(value, dict):
            raise ValueError(f"规则字段不是结构化事实: {path}")
        return value

    @staticmethod
    def _is_missing(fact: dict) -> bool:
        return fact.get("extract_status") in {"not_found", "failed"} or not fact.get("value")

    @staticmethod
    def _metric_values(metric: str, text: str) -> list[float]:
        if metric == "prepayment_ratio":
            pattern = re.compile(
                r"(?:预付款(?:比例)?(?:为|[:：])?\s*(\d+(?:\.\d+)?)\s*%|"
                r"(\d+(?:\.\d+)?)\s*%\s*(?:的)?预付款)"
            )
        elif metric == "payment_period_days":
            pattern = re.compile(
                r"(?:付款|支付|款项)[^。；\n]{0,40}?(\d+)\s*(?:个)?(?:自然日|工作日|日|天)|"
                r"(\d+)\s*(?:个)?(?:自然日|工作日|日|天)[^。；\n]{0,20}?(?:付款|支付|款项)"
            )
        else:
            raise ValueError(f"不支持的数值指标: {metric}")
        return [float(value) for groups in pattern.findall(text) for value in groups if value]

    @staticmethod
    def _format_number(value: float) -> str:
        return str(int(value)) if value.is_integer() else str(value)

    @staticmethod
    def _match(
        rule: ReviewRule,
        *,
        evidence_text: str,
        evidence_position: dict | None,
        evidence_type: EvidenceType,
        actual_value: str | None,
        expected_value: str | None,
        hit_message: str,
    ) -> RuleMatch:
        return RuleMatch(
            rule_id=rule.id,
            rule_version=rule.rule_version,
            evidence_text=evidence_text,
            evidence_position=evidence_position,
            evidence_type=evidence_type,
            actual_value=actual_value,
            expected_value=expected_value,
            hit_message=hit_message,
        )
