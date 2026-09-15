import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MatchMode, ReviewRule, RiskLevel, RuleStatus


def _config(**values: object) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


DEFAULT_REVIEW_RULES = (
    {
        "rule_code": "SUBJECT_INFO_MISSING",
        "rule_name": "合同主体信息缺失",
        "risk_level": RiskLevel.HIGH,
        "match_mode": MatchMode.FIELD_MISSING,
        "match_text": _config(
            fields=["basic_info.signing_party", "basic_info.counterparty"],
            trigger="any",
            hit_message="未提取到甲方或乙方信息",
        ),
        "suggestion_text": "请补充并核对合同双方的完整法定名称。",
    },
    {
        "rule_code": "AMOUNT_MISSING",
        "rule_name": "合同金额缺失",
        "risk_level": RiskLevel.MEDIUM,
        "match_mode": MatchMode.FIELD_MISSING,
        "match_text": _config(
            fields=["basic_info.amount"],
            trigger="any",
            hit_message="未提取到合同金额",
        ),
        "suggestion_text": "请明确合同总金额及计价口径。",
    },
    {
        "rule_code": "CONFIDENTIALITY_MISSING",
        "rule_name": "保密条款缺失",
        "risk_level": RiskLevel.MEDIUM,
        "match_mode": MatchMode.FIELD_MISSING,
        "match_text": _config(
            fields=["clauses.confidentiality_clause"],
            trigger="any",
            hit_message="未提取到保密条款",
        ),
        "suggestion_text": "建议补充保密范围、期限及违约责任。",
    },
    {
        "rule_code": "ACCEPTANCE_STANDARD_MISSING",
        "rule_name": "验收条款缺失",
        "risk_level": RiskLevel.MEDIUM,
        "match_mode": MatchMode.FIELD_MISSING,
        "match_text": _config(
            fields=["clauses.acceptance_clause"],
            trigger="any",
            hit_message="未提取到验收条款",
        ),
        "suggestion_text": "建议明确验收标准、期限和不通过后的处理方式。",
    },
    {
        "rule_code": "PAYMENT_TERM_MISSING",
        "rule_name": "付款条款缺失",
        "risk_level": RiskLevel.MEDIUM,
        "match_mode": MatchMode.FIELD_MISSING,
        "match_text": _config(
            fields=["clauses.payment_clause"],
            trigger="any",
            hit_message="未提取到付款条款",
        ),
        "suggestion_text": "建议明确付款节点、比例、期限和前置条件。",
    },
    {
        "rule_code": "PREPAYMENT_RATIO_HIGH",
        "rule_name": "预付款比例过高",
        "risk_level": RiskLevel.HIGH,
        "match_mode": MatchMode.NUMERIC_THRESHOLD,
        "match_text": _config(
            field="clauses.payment_clause",
            metric="prepayment_ratio",
            operator="gt",
            threshold=50,
            unit="percent",
            hit_message="预付款比例高于 50%",
        ),
        "suggestion_text": "建议降低预付款比例或增加履约担保与付款条件。",
    },
    {
        "rule_code": "PAYMENT_PERIOD_LONG",
        "rule_name": "付款周期过长",
        "risk_level": RiskLevel.MEDIUM,
        "match_mode": MatchMode.NUMERIC_THRESHOLD,
        "match_text": _config(
            field="clauses.payment_clause",
            metric="payment_period_days",
            operator="gt",
            threshold=60,
            unit="days",
            hit_message="付款周期超过 60 天",
        ),
        "suggestion_text": "建议确认长付款周期的资金占用影响并协商缩短。",
    },
    {
        "rule_code": "AUTO_RENEWAL_PRESENT",
        "rule_name": "存在自动续约约定",
        "risk_level": RiskLevel.MEDIUM,
        "match_mode": MatchMode.KEYWORD,
        "match_text": _config(
            fields=[
                "clauses.payment_clause",
                "clauses.delivery_clause",
                "clauses.acceptance_clause",
                "clauses.breach_clause",
                "clauses.confidentiality_clause",
                "clauses.data_clause",
                "clauses.intellectual_property_clause",
                "clauses.dispute_resolution_clause",
            ],
            keywords=["自动续约", "自动延长", "自动顺延"],
            hit_message="发现自动续约或自动延长表达",
        ),
        "suggestion_text": "建议核对自动续约期限、通知窗口和退出条件。",
    },
    {
        "rule_code": "DISPUTE_JURISDICTION_PRESENT",
        "rule_name": "争议管辖信息已识别",
        "risk_level": RiskLevel.LOW,
        "match_mode": MatchMode.PRESENCE,
        "match_text": _config(
            fields=["clauses.dispute_resolution_clause"],
            keywords=["人民法院", "管辖", "仲裁委员会"],
            hit_message="已识别明确的争议解决机构或管辖表达",
        ),
        "suggestion_text": "请结合双方所在地和履约成本人工确认该管辖约定是否有利。",
    },
)


def seed_default_review_rules(session: Session) -> int:
    """只补充缺失规则，不覆盖用户后续对已有规则的配置。"""

    existing_codes = set(session.scalars(select(ReviewRule.rule_code)))
    created_count = 0
    for definition in DEFAULT_REVIEW_RULES:
        if definition["rule_code"] in existing_codes:
            continue
        session.add(
            ReviewRule(
                **definition,
                rule_status=RuleStatus.ACTIVE,
                rule_version="1.0",
            )
        )
        created_count += 1
    session.flush()
    return created_count
