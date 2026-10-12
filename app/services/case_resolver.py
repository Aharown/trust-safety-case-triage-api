from sqlalchemy.orm import Session
from app.models import Case, CaseState
from app.services.case_transitions import transition_case


def resolve_case(db: Session, case: Case, resolution: str) -> Case:
    return transition_case(
        db, case, CaseState.resolved, event_type="resolved", notes=resolution
    )


def start_review(db: Session, case: Case) -> Case:
    return transition_case(db, case, CaseState.in_review, event_type="review_started")
