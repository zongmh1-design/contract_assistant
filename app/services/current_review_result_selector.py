from sqlalchemy.orm import Session

from app.models import ReviewResult
from app.repositories import (
    ReviewResultRepository,
    ReviewRuleRepository,
    RuleHitRepository,
)
from app.rules import rule_hit_fingerprint, rule_set_fingerprint
from app.services.contract_parse_selector import (
    ContractParseNotFoundError,
    ContractParseSelector,
)


class CurrentReviewResultNotFoundError(LookupError):
    pass


class CurrentReviewResultSelector:
    """只选择当前解析、当前规则集合与当前命中集合对应的完成结果。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def select_for_task(self, task_id: int) -> ReviewResult:
        try:
            contract_parse = ContractParseSelector(self.session).select_for_task(task_id)
        except ContractParseNotFoundError as error:
            raise CurrentReviewResultNotFoundError(
                "REVIEW_RESULT_NOT_FOUND: 当前合同没有有效 ContractParse"
            ) from error
        rules = ReviewRuleRepository(self.session).list_active_rules()
        hits = RuleHitRepository(self.session).list_current_for_parse(contract_parse.id)
        result = ReviewResultRepository(self.session).get_latest_current(
            contract_parse.id,
            rule_set_fingerprint(rules),
            rule_hit_fingerprint(hits),
        )
        if result is None:
            raise CurrentReviewResultNotFoundError(
                "REVIEW_RESULT_NOT_FOUND: 当前解析、规则和命中集合没有 completed 审查结果"
            )
        return result
