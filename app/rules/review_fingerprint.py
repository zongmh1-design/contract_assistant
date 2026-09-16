import hashlib

from app.models import LlmRuleEvaluation, ReviewRule, RuleHit


def rule_set_fingerprint(rules: list[ReviewRule]) -> str:
    """标识一次审查应执行的 active 规则代码及显式版本。"""

    payload = "\n".join(
        sorted(f"{rule.rule_code}@{rule.rule_version}" for rule in rules)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def rule_hit_fingerprint(
    hits: list[RuleHit], evaluations: list[LlmRuleEvaluation] | None = None
) -> str:
    """标识一次汇总实际使用的不可变 RuleHit 集合。"""

    hit_payload = "\n".join(
        sorted(f"{hit.id}@{hit.rule_id}@{hit.rule_version}" for hit in hits)
    )
    if not evaluations:
        return hashlib.sha256(hit_payload.encode("utf-8")).hexdigest()
    evaluation_payload = "\n".join(
        sorted(
            f"{item.rule_id}@{item.rule_version}@{item.evaluation_fingerprint}@"
            f"{item.evaluation_status}@{item.decision}"
            for item in (evaluations or [])
        )
    )
    return hashlib.sha256(f"{hit_payload}\n--semantic--\n{evaluation_payload}".encode("utf-8")).hexdigest()
