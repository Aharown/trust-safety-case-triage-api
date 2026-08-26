from sqlalchemy.orm import Session
from app.models import Case, CaseEvent, CaseState
from app.state_machine import is_valid_transition, InvalidTransitionError


def transition_case(db: Session, case: Case, to_state: CaseState, event_type: str, notes: str = None) -> Case:
    if not is_valid_transition(case.state, to_state):
        raise InvalidTransitionError(f"Cannot move from {case.state} to {to_state}")

    from_state = case.state
    case.state = to_state
    db.add(case)
    db.add(CaseEvent(case_id=case.id, event_type=event_type, from_state=from_state, to_state=to_state, notes=notes))
    db.commit()
    db.refresh(case)
    return case
