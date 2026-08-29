from app.state_machine import is_valid_transition
from app.models import CaseState


def test_valid_transition_new_to_pending():
    assert is_valid_transition(CaseState.new, CaseState.pending_classification) is True


def test_valid_transition_classified_to_routed():
    assert is_valid_transition(CaseState.classified, CaseState.routed) is True


def test_invalid_transition_new_to_resolved():
    assert is_valid_transition(CaseState.new, CaseState.resolved) is False


def test_invalid_transition_resolved_to_routed():
    assert is_valid_transition(CaseState.resolved, CaseState.routed) is False


def test_escalated_can_return_to_in_review():
    assert is_valid_transition(CaseState.escalated, CaseState.in_review) is True
