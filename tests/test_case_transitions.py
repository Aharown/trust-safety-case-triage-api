from app.models import Case, CaseState
from app.services.case_transitions import transition_case
from app.state_machine import InvalidTransitionError
import pytest


def test_transition_creates_case_event(db):
    case = Case(description="test case", state=CaseState.new)
    db.add(case)
    db.commit()
    db.refresh(case)

    updated = transition_case(db, case, CaseState.pending_classification, event_type="classification_requested")

    assert updated.state == CaseState.pending_classification


def test_invalid_transition_raises(db):
    case = Case(description="test case", state=CaseState.new)
    db.add(case)
    db.commit()
    db.refresh(case)

    with pytest.raises(InvalidTransitionError):
        transition_case(db, case, CaseState.resolved, event_type="invalid_attempt")
