import hashlib

from app.models import ReviewRule, RuleHit


def rule_set_fingerprint(rules: list[ReviewRule]) -> str:
    """标识一次审查应执行的 active 规则代码及显式版本。"""

    payload = "\n".join(
        sorted(f"{rule.rule_code}@{rule.rule_version}" for rule in rules)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def rule_hit_fingerprint(hits: list[RuleHit]) -> str:
    """标识一次汇总实际使用的不可变 RuleHit 集合。"""

    payload = "\n".join(
        sorted(f"{hit.id}@{hit.rule_id}@{hit.rule_version}" for hit in hits)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
