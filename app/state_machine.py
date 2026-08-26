from app.models import CaseState

VALID_TRANSITIONS = {
    CaseState.new: {CaseState.pending_classification},
    CaseState.pending_classification: {CaseState.classified, CaseState.new},
    CaseState.classified: {CaseState.routed},
    CaseState.routed: {CaseState.in_review},
    CaseState.in_review: {CaseState.escalated, CaseState.resolved},
    CaseState.escalated: {CaseState.in_review, CaseState.resolved},
    CaseState.resolved: {CaseState.reopened},
    CaseState.reopened: {CaseState.in_review},
}


def is_valid_transition(from_state: CaseState, to_state: CaseState) -> bool:
    return to_state in VALID_TRANSITIONS.get(from_state, set())


class InvalidTransitionError(Exception):
    pass
