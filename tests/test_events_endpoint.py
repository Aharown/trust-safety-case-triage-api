from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import (
    Case,
    CaseEvent,
    CaseState,
    ReportedEntityType,
)
from tests.conftest import (
    TestSessionLocal,
    override_get_db,
    override_get_db_committing,
)

client = TestClient(app)
AGENT = {"X-Role": "agent"}


def _make_case(state=CaseState.resolved):
    db = TestSessionLocal()
    case = Case(
        description="test",
        reported_entity_type=ReportedEntityType.listing,
        reported_entity_id=1,
        state=state,
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    case_id = case.id
    db.close()
    return case_id


def _add_event(case_id, event_type, from_state, to_state, created_at, notes=None):
    db = TestSessionLocal()
    db.add(
        CaseEvent(
            case_id=case_id,
            event_type=event_type,
            from_state=from_state,
            to_state=to_state,
            notes=notes,
            created_at=created_at,
        )
    )
    db.commit()
    db.close()


def _cleanup_case(case_id):
    if case_id is None:
        return
    db = TestSessionLocal()
    db.query(CaseEvent).filter(CaseEvent.case_id == case_id).delete()
    db.query(Case).filter(Case.id == case_id).delete()
    db.commit()
    db.close()


def test_events_returns_404_for_unknown_case():
    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/cases/999999/events", headers=AGENT)
        assert response.status_code == 404
    finally:
        app.dependency_overrides[get_db] = override_get_db


def test_events_rejects_non_agent_role():
    app.dependency_overrides[get_db] = override_get_db
    response = client.get("/cases/1/events", headers={"X-Role": "reporter"})
    assert response.status_code == 403


def test_events_returns_empty_list_for_case_with_no_events():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case(CaseState.new)
        response = client.get(f"/cases/{case_id}/events", headers=AGENT)
        assert response.status_code == 200
        assert response.json() == []
    finally:
        _cleanup_case(case_id)
        app.dependency_overrides[get_db] = override_get_db


def test_events_are_returned_oldest_first():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case()
        start = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
        # Inserted out of order on purpose, so only real sorting passes.
        _add_event(
            case_id, "resolved", CaseState.in_review, CaseState.resolved,
            start + timedelta(minutes=30), notes="Removed listing",
        )
        _add_event(
            case_id, "routed", CaseState.classified, CaseState.routed,
            start + timedelta(minutes=10),
        )
        _add_event(
            case_id, "review_started", CaseState.routed, CaseState.in_review,
            start + timedelta(minutes=20),
        )

        response = client.get(f"/cases/{case_id}/events", headers=AGENT)

        assert response.status_code == 200
        types = [e["event_type"] for e in response.json()]
        assert types == ["routed", "review_started", "resolved"]
    finally:
        _cleanup_case(case_id)
        app.dependency_overrides[get_db] = override_get_db


def test_events_with_same_timestamp_fall_back_to_id_order():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case()
        same_time = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
        _add_event(case_id, "first", None, CaseState.new, same_time)
        _add_event(case_id, "second", CaseState.new, CaseState.classified, same_time)
        _add_event(case_id, "third", CaseState.classified, CaseState.routed, same_time)

        response = client.get(f"/cases/{case_id}/events", headers=AGENT)

        types = [e["event_type"] for e in response.json()]
        assert types == ["first", "second", "third"]
    finally:
        _cleanup_case(case_id)
        app.dependency_overrides[get_db] = override_get_db


def test_events_only_include_the_requested_case():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_a = case_b = None
    try:
        case_a = _make_case()
        case_b = _make_case()
        when = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
        _add_event(case_a, "routed", CaseState.classified, CaseState.routed, when)
        _add_event(case_b, "escalated", CaseState.routed, CaseState.escalated, when)

        response = client.get(f"/cases/{case_a}/events", headers=AGENT)

        body = response.json()
        assert [e["event_type"] for e in body] == ["routed"]
        assert all(e["case_id"] == case_a for e in body)
    finally:
        _cleanup_case(case_a)
        _cleanup_case(case_b)
        app.dependency_overrides[get_db] = override_get_db


def test_events_response_has_expected_fields_and_values():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case()
        when = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
        _add_event(
            case_id, "resolved", CaseState.in_review, CaseState.resolved,
            when, notes="Removed listing",
        )

        response = client.get(f"/cases/{case_id}/events", headers=AGENT)

        event = response.json()[0]
        assert set(event) == {
            "id", "case_id", "event_type", "from_state",
            "to_state", "notes", "created_at",
        }
        assert event["from_state"] == "in_review"
        assert event["to_state"] == "resolved"
        assert event["notes"] == "Removed listing"
    finally:
        _cleanup_case(case_id)
        app.dependency_overrides[get_db] = override_get_db


def test_events_are_still_readable_for_a_resolved_case():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case(CaseState.resolved)
        _add_event(
            case_id, "resolved", CaseState.in_review, CaseState.resolved,
            datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
        )
        response = client.get(f"/cases/{case_id}/events", headers=AGENT)
        assert response.status_code == 200
        assert len(response.json()) == 1
    finally:
        _cleanup_case(case_id)
        app.dependency_overrides[get_db] = override_get_db
