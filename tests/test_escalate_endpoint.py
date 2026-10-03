import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import (
    Case,
    CaseEvent,
    CaseState,
    ReportedEntityType,
    Severity,
    Category,
    Queue,
)
from tests.conftest import (
    TestSessionLocal,
    override_get_db,
    override_get_db_committing,
)

client = TestClient(app)
AGENT = {"X-Role": "agent"}


def _make_case(state, queue=Queue.fraud):
    db = TestSessionLocal()
    case = Case(
        description="test",
        reported_entity_type=ReportedEntityType.listing,
        reported_entity_id=1,
        state=state,
        severity=Severity.high,
        category=Category.fraud,
        queue=queue,
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


@pytest.mark.parametrize("state", [CaseState.routed, CaseState.in_review])
def test_escalate_succeeds_from_eligible_states(state):
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case(state)

        response = client.post(
            f"/cases/{case_id}/escalate",
            json={"reason": "Reviewer concerned about repeat offender"},
            headers=AGENT,
        )

        assert response.status_code == 200
        assert response.json()["state"] == "escalated"
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


def test_escalate_writes_event_with_reason():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case(CaseState.in_review)

        client.post(
            f"/cases/{case_id}/escalate",
            json={"reason": "Possible ongoing scam"},
            headers=AGENT,
        )

        db = TestSessionLocal()
        event = (
            db.query(CaseEvent)
            .filter(CaseEvent.case_id == case_id, CaseEvent.event_type == "escalated")
            .one()
        )
        assert event.from_state == CaseState.in_review
        assert event.to_state == CaseState.escalated
        assert event.notes == "Manually escalated: Possible ongoing scam"
        db.close()
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


def test_escalate_does_not_change_queue():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case(CaseState.routed, queue=Queue.prohibited_items)

        response = client.post(
            f"/cases/{case_id}/escalate",
            json={"reason": "Urgent"},
            headers=AGENT,
        )

        assert response.status_code == 200
        assert response.json()["queue"] == "prohibited_items"
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


@pytest.mark.parametrize(
    "state",
    [CaseState.new, CaseState.classified, CaseState.escalated, CaseState.resolved],
)
def test_escalate_rejects_ineligible_states(state):
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case(state)

        response = client.post(
            f"/cases/{case_id}/escalate",
            json={"reason": "Should not work"},
            headers=AGENT,
        )

        assert response.status_code == 409

        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        assert case.state == state
        events = (
            db.query(CaseEvent)
            .filter(CaseEvent.case_id == case_id, CaseEvent.event_type == "escalated")
            .all()
        )
        assert events == []
        db.close()
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


def test_escalate_404_for_missing_case():
    response = client.post(
        "/cases/999999/escalate", json={"reason": "Anything"}, headers=AGENT
    )
    assert response.status_code == 404


@pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": "   "}])
def test_escalate_requires_a_real_reason(body):
    response = client.post("/cases/1/escalate", json=body, headers=AGENT)
    assert response.status_code == 422


def test_escalate_without_role_header_fails():
    response = client.post("/cases/1/escalate", json={"reason": "Anything"})
    assert response.status_code == 422


def test_escalate_as_reporter_forbidden():
    response = client.post(
        "/cases/1/escalate",
        json={"reason": "Anything"},
        headers={"X-Role": "reporter"},
    )
    assert response.status_code == 403
