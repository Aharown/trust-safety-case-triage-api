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


@pytest.mark.parametrize("state", [CaseState.in_review, CaseState.escalated])
def test_resolve_succeeds_from_eligible_states(state):
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case(state)

        response = client.post(
            f"/cases/{case_id}/resolve",
            json={"resolution": "Listing removed, seller warned"},
            headers=AGENT,
        )

        assert response.status_code == 200
        assert response.json()["state"] == "resolved"
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


def test_resolve_writes_event_with_resolution():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case(CaseState.in_review)

        client.post(
            f"/cases/{case_id}/resolve",
            json={"resolution": "No violation found"},
            headers=AGENT,
        )

        db = TestSessionLocal()
        event = (
            db.query(CaseEvent)
            .filter(CaseEvent.case_id == case_id, CaseEvent.event_type == "resolved")
            .one()
        )
        assert event.from_state == CaseState.in_review
        assert event.to_state == CaseState.resolved
        assert event.notes == "No violation found"
        db.close()
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


def test_resolve_does_not_change_queue():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case(CaseState.in_review, queue=Queue.prohibited_items)

        response = client.post(
            f"/cases/{case_id}/resolve",
            json={"resolution": "Item removed"},
            headers=AGENT,
        )

        assert response.status_code == 200
        assert response.json()["queue"] == "prohibited_items"
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


@pytest.mark.parametrize(
    "state",
    [
        CaseState.new,
        CaseState.classified,
        CaseState.routed,
        CaseState.resolved,
        CaseState.reopened,
    ],
)
def test_resolve_rejects_ineligible_states(state):
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case(state)

        response = client.post(
            f"/cases/{case_id}/resolve",
            json={"resolution": "Should not work"},
            headers=AGENT,
        )

        assert response.status_code == 409

        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        assert case.state == state
        events = (
            db.query(CaseEvent)
            .filter(CaseEvent.case_id == case_id, CaseEvent.event_type == "resolved")
            .all()
        )
        assert events == []
        db.close()
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


def test_resolve_404_for_missing_case():
    response = client.post(
        "/cases/999999/resolve", json={"resolution": "Anything"}, headers=AGENT
    )
    assert response.status_code == 404


@pytest.mark.parametrize("body", [{}, {"resolution": ""}, {"resolution": "   "}])
def test_resolve_requires_a_real_resolution(body):
    response = client.post("/cases/1/resolve", json=body, headers=AGENT)
    assert response.status_code == 422


def test_resolve_without_role_header_fails():
    response = client.post("/cases/1/resolve", json={"resolution": "Anything"})
    assert response.status_code == 422


def test_resolve_as_reporter_forbidden():
    response = client.post(
        "/cases/1/resolve",
        json={"resolution": "Anything"},
        headers={"X-Role": "reporter"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "state", [CaseState.routed, CaseState.reopened, CaseState.escalated]
)
def test_pickup_moves_case_to_in_review(state):
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case(state)

        response = client.post(f"/cases/{case_id}/pickup", headers=AGENT)

        assert response.status_code == 200
        assert response.json()["state"] == "in_review"

        db = TestSessionLocal()
        event = (
            db.query(CaseEvent)
            .filter(
                CaseEvent.case_id == case_id, CaseEvent.event_type == "review_started"
            )
            .one()
        )
        assert event.from_state == state
        assert event.to_state == CaseState.in_review
        db.close()
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


@pytest.mark.parametrize(
    "state", [CaseState.new, CaseState.classified, CaseState.in_review, CaseState.resolved]
)
def test_pickup_rejects_ineligible_states(state):
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case(state)

        response = client.post(f"/cases/{case_id}/pickup", headers=AGENT)

        assert response.status_code == 409

        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        assert case.state == state
        db.close()
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


def test_pickup_404_for_missing_case():
    response = client.post("/cases/999999/pickup", headers=AGENT)
    assert response.status_code == 404


def test_pickup_as_reporter_forbidden():
    response = client.post("/cases/1/pickup", headers={"X-Role": "reporter"})
    assert response.status_code == 403
