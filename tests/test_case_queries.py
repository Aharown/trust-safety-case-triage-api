from datetime import datetime, timedelta, timezone

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
from app.services.case_queries import time_in_state_hours
from tests.conftest import (
    TestSessionLocal,
    override_get_db,
    override_get_db_committing,
)

client = TestClient(app)
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def _make_case(state, severity, queue=Queue.fraud):
    db = TestSessionLocal()
    case = Case(
        description="test",
        reported_entity_type=ReportedEntityType.listing,
        reported_entity_id=1,
        state=state,
        severity=severity,
        category=Category.fraud,
        queue=queue,
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    case_id = case.id
    db.close()
    return case_id


def _cleanup(case_ids):
    db = TestSessionLocal()
    db.query(CaseEvent).filter(CaseEvent.case_id.in_(case_ids)).delete(
        synchronize_session=False
    )
    db.query(Case).filter(Case.id.in_(case_ids)).delete(synchronize_session=False)
    db.commit()
    db.close()


def test_time_in_state_uses_entry_event_when_present():
    case = Case(created_at=NOW - timedelta(days=5))
    assert time_in_state_hours(case, NOW - timedelta(hours=2), NOW) == 2.0


def test_time_in_state_falls_back_to_created_at():
    case = Case(created_at=NOW - timedelta(hours=1))
    assert time_in_state_hours(case, None, NOW) == 1.0


def test_time_in_state_keeps_one_decimal():
    case = Case(created_at=NOW - timedelta(days=5))
    assert time_in_state_hours(case, NOW - timedelta(minutes=90), NOW) == 1.5


def test_list_orders_escalated_then_severity_then_oldest():
    app.dependency_overrides[get_db] = override_get_db_committing
    ids = []
    try:
        low = _make_case(CaseState.routed, Severity.low)
        critical = _make_case(CaseState.routed, Severity.critical)
        escalated_low = _make_case(CaseState.escalated, Severity.low)
        high = _make_case(CaseState.in_review, Severity.high)
        ids = [low, critical, escalated_low, high]

        response = client.get("/cases", headers={"X-Role": "agent"})
        assert response.status_code == 200

        returned = [c["id"] for c in response.json() if c["id"] in ids]
        assert returned == [escalated_low, critical, high, low]
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup(ids)


def test_list_filters_by_queue():
    app.dependency_overrides[get_db] = override_get_db_committing
    ids = []
    try:
        fraud = _make_case(CaseState.routed, Severity.high, queue=Queue.fraud)
        general = _make_case(CaseState.routed, Severity.high, queue=Queue.general)
        ids = [fraud, general]

        response = client.get("/cases?queue=fraud", headers={"X-Role": "agent"})
        returned = [c["id"] for c in response.json()]
        assert fraud in returned
        assert general not in returned
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup(ids)


def test_list_rejects_invalid_queue_value():
    response = client.get("/cases?queue=billing_team", headers={"X-Role": "agent"})
    assert response.status_code == 422


def test_list_includes_time_in_state():
    app.dependency_overrides[get_db] = override_get_db_committing
    ids = []
    try:
        case_id = _make_case(CaseState.routed, Severity.high)
        ids = [case_id]

        response = client.get("/cases", headers={"X-Role": "agent"})
        mine = next(c for c in response.json() if c["id"] == case_id)
        assert mine["time_in_state_hours"] >= 0
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup(ids)
