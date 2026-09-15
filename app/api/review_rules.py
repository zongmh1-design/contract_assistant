from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.tasks import get_session
from app.repositories import ReviewRuleRepository
from app.schemas import ReviewRuleRead


router = APIRouter(prefix="/api/review-rules", tags=["review-rules"])


@router.get("", response_model=list[ReviewRuleRead])
def list_review_rules(
    session: Session = Depends(get_session),
) -> list[ReviewRuleRead]:
    return [
        ReviewRuleRead.model_validate(rule)
        for rule in ReviewRuleRepository(session).list_rules()
    ]
