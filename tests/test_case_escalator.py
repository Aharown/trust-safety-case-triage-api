import pytest
from tests.conftest import TestSessionLocal
from app.models import (
    Case,
    CaseEvent,
    CaseState,
    ReportedEntityType,
    Category,
    Severity,
)
from app.services.case_escalator import (
    should_auto_escalate_critical,
    case_was_escalated_before,
    escalate_case,
    auto_escalate_if_critical,
)
from app.state_machine import InvalidTransitionError


def _make_case(state, severity=Severity.critical):
    db = TestSessionLocal()
    case = Case(
        description="test",
        reported_entity_type=ReportedEntityType.listing,
        reported_entity_id=1,
        state=state,
        severity=severity,
        category=Category.fraud,
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    case_id = case.id
    db.close()
    return case_id


def _cleanup_case(case_id):
    if case_id is None:
        return
    db = TestSessionLocal()
    db.query(CaseEvent).filter(CaseEvent.case_id == case_id).delete()
    db.query(Case).filter(Case.id == case_id).delete()
    db.commit()
    db.close()


def test_critical_routed_no_history_escalates():
    case = Case(severity=Severity.critical, state=CaseState.routed)
    assert should_auto_escalate_critical(case, was_escalated_before=False) is True


def test_critical_with_prior_escalation_does_not():
    case = Case(severity=Severity.critical, state=CaseState.routed)
    assert should_auto_escalate_critical(case, was_escalated_before=True) is False


@pytest.mark.parametrize("severity", [Severity.low, Severity.medium, Severity.high])
def test_non_critical_never_auto_escalates(severity):
    case = Case(severity=severity, state=CaseState.routed)
    assert should_auto_escalate_critical(case, was_escalated_before=False) is False


def test_critical_outside_routed_does_not():
    case = Case(severity=Severity.critical, state=CaseState.in_review)
    assert should_auto_escalate_critical(case, was_escalated_before=False) is False


def test_case_was_escalated_before_false_with_no_events():
    case_id = _make_case(CaseState.routed)
    try:
        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        assert case_was_escalated_before(db, case) is False
        db.close()
    finally:
        _cleanup_case(case_id)


def test_case_was_escalated_before_true_after_escalation():
    case_id = _make_case(CaseState.routed)
    try:
        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        escalate_case(db, case, reason="test")
        assert case_was_escalated_before(db, case) is True
        db.close()
    finally:
        _cleanup_case(case_id)


def test_escalate_case_changes_state_and_writes_event():
    case_id = _make_case(CaseState.routed)
    try:
        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        result = escalate_case(db, case, reason="because")
        assert result.state == CaseState.escalated

        event = (
            db.query(CaseEvent)
            .filter(CaseEvent.case_id == case_id, CaseEvent.event_type == "escalated")
            .one()
        )
        assert event.from_state == CaseState.routed
        assert event.to_state == CaseState.escalated
        assert event.notes == "because"
        db.close()
    finally:
        _cleanup_case(case_id)


def test_escalate_case_rejects_invalid_state():
    case_id = _make_case(CaseState.new)
    try:
        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        with pytest.raises(InvalidTransitionError):
            escalate_case(db, case, reason="nope")
        db.close()
    finally:
        _cleanup_case(case_id)


def test_auto_escalate_if_critical_escalates():
    case_id = _make_case(CaseState.routed)
    try:
        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        result = auto_escalate_if_critical(db, case)
        assert result.state == CaseState.escalated
        db.close()
    finally:
        _cleanup_case(case_id)


def test_auto_escalate_skips_case_with_prior_escalation():
    case_id = _make_case(CaseState.routed)
    try:
        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        db.add(
            CaseEvent(
                case_id=case_id,
                event_type="escalated",
                from_state=CaseState.in_review,
                to_state=CaseState.escalated,
            )
        )
        db.commit()
        result = auto_escalate_if_critical(db, case)
        assert result.state == CaseState.routed
        db.close()
    finally:
        _cleanup_case(case_id)


def test_auto_escalate_leaves_high_severity_alone():
    case_id = _make_case(CaseState.routed, severity=Severity.high)
    try:
        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        result = auto_escalate_if_critical(db, case)
        assert result.state == CaseState.routed
        db.close()
    finally:
        _cleanup_case(case_id)
