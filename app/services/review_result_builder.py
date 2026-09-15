from dataclasses import dataclass
from typing import Protocol

from app.models import ReviewStatus, RiskLevel, RuleHit


@dataclass(frozen=True)
class BuiltReviewResult:
    overall_risk_level: RiskLevel
    summary_text: str
    focus_points: list[dict]
    comment_text: str
    review_status: ReviewStatus


class ReviewResultBuilder(Protocol):
    version: str

    def build(self, hits: list[RuleHit]) -> BuiltReviewResult: ...


class DeterministicReviewResultBuilder:
    version = "1.0"

    def build(self, hits: list[RuleHit]) -> BuiltReviewResult:
        ordered_hits = sorted(
            hits,
            key=lambda hit: (
                -self._risk_weight(hit.rule.risk_level),
                hit.rule.rule_code,
                hit.id,
            ),
        )
        counts = {
            level: sum(hit.rule.risk_level == level for hit in ordered_hits)
            for level in RiskLevel
        }
        overall = self._overall_risk(counts)
        focus_points = [self._focus_point(hit) for hit in ordered_hits]
        summary = self._summary_text(ordered_hits, counts)
        comment = self._comment_text(overall, summary, focus_points)
        return BuiltReviewResult(
            overall_risk_level=overall,
            summary_text=summary,
            focus_points=focus_points,
            comment_text=comment,
            review_status=ReviewStatus.COMPLETED,
        )

    @staticmethod
    def _focus_point(hit: RuleHit) -> dict:
        return {
            "rule_code": hit.rule.rule_code,
            "rule_name": hit.rule.rule_name,
            "risk_level": hit.rule.risk_level.value,
            "message": hit.hit_message,
            "suggestion": hit.rule.suggestion_text,
            "evidence_type": hit.evidence_type.value,
            "evidence_text": hit.evidence_text,
            "evidence_position": hit.evidence_position,
        }

    @classmethod
    def _summary_text(cls, hits: list[RuleHit], counts: dict[RiskLevel, int]) -> str:
        if not hits:
            return "本合同未命中当前确定性风险规则，整体风险等级为低。"
        names = "、".join(hit.rule.rule_name for hit in hits[:3])
        return (
            f"本合同共命中 {len(hits)} 条风险规则，其中高风险 "
            f"{counts[RiskLevel.HIGH]} 条、中风险 {counts[RiskLevel.MEDIUM]} 条、"
            f"低风险 {counts[RiskLevel.LOW]} 条。建议重点关注{names}。"
        )

    @staticmethod
    def _comment_text(
        overall: RiskLevel, summary: str, focus_points: list[dict]
    ) -> str:
        level_names = {
            RiskLevel.HIGH: "高风险",
            RiskLevel.MEDIUM: "中风险",
            RiskLevel.LOW: "低风险",
        }
        lines = [f"合同审查结果：{level_names[overall]}", "", summary]
        if not focus_points:
            lines.extend(["", "当前确定性规则未发现明确风险，请继续进行人工审批复核。"])
            return "\n".join(lines)
        lines.extend(["", "重点关注："])
        lines.extend(
            f"{index}. {item['rule_name']}：{item['message']}"
            for index, item in enumerate(focus_points, start=1)
        )
        lines.extend(["", "建议："])
        lines.extend(
            f"{index}. {item['suggestion']}"
            for index, item in enumerate(focus_points, start=1)
        )
        return "\n".join(lines)

    @staticmethod
    def _overall_risk(counts: dict[RiskLevel, int]) -> RiskLevel:
        if counts[RiskLevel.HIGH]:
            return RiskLevel.HIGH
        if counts[RiskLevel.MEDIUM]:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    @staticmethod
    def _risk_weight(level: RiskLevel) -> int:
        return {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3}[level]
