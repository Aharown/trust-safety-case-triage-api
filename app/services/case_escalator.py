from sqlalchemy.orm import Session

from app.models import Case, CaseEvent, CaseState, Severity
from app.services.case_transitions import transition_case


def should_auto_escalate_critical(case: Case, was_escalated_before: bool) -> bool:
    return (
        case.severity == Severity.critical
        and case.state == CaseState.routed
        and not was_escalated_before
    )


def case_was_escalated_before(db: Session, case: Case) -> bool:
    return (
        db.query(CaseEvent.id)
        .filter(
            CaseEvent.case_id == case.id,
            CaseEvent.to_state == CaseState.escalated,
        )
        .first()
        is not None
    )


def escalate_case(db: Session, case: Case, reason: str) -> Case:
    return transition_case(
        db,
        case,
        CaseState.escalated,
        event_type="escalated",
        notes=reason,
    )


def auto_escalate_if_critical(db: Session, case: Case) -> Case:
    if should_auto_escalate_critical(case, case_was_escalated_before(db, case)):
        return escalate_case(db, case, reason="Auto-escalated: severity is critical.")
    return case
