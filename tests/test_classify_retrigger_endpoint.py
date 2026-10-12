from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import (
    AiClassification,
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

CLASSIFIER = "app.services.ai_classifier.classify_case_description"


def _ai_success(severity=Severity.high, category=Category.fraud):
    return SimpleNamespace(
        severity=severity,
        category=category,
        confidence=0.9,
        raw_response="{}",
        succeeded=True,
    )


def _ai_failure():
    return SimpleNamespace(
        severity=None,
        category=None,
        confidence=None,
        raw_response="timeout",
        succeeded=False,
    )


def _make_case(state=CaseState.in_review, queue=Queue.manual_triage):
    db = TestSessionLocal()
    case = Case(
        description="test",
        reported_entity_type=ReportedEntityType.listing,
        reported_entity_id=1,
        state=state,
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
    db.query(AiClassification).filter(AiClassification.case_id == case_id).delete()
    db.query(CaseEvent).filter(CaseEvent.case_id == case_id).delete()
    db.query(Case).filter(Case.id == case_id).delete()
    db.commit()
    db.close()


def _retrigger(case_id, ai_result):
    with patch(CLASSIFIER, return_value=ai_result) as mock_classifier, patch(
        "app.main.SessionLocal", TestSessionLocal
    ):
        response = client.post(f"/cases/{case_id}/classify", headers=AGENT)
    return response, mock_classifier


def test_retrigger_response_shows_pending_classification():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case()

        response, _ = _retrigger(case_id, _ai_success())

        assert response.status_code == 200
        assert response.json()["state"] == "pending_classification"
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


def test_retrigger_success_classifies_and_routes_the_case():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case()

        _retrigger(case_id, _ai_success(Severity.high, Category.fraud))

        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        assert case.state == CaseState.routed
        assert case.queue == Queue.fraud
        assert case.severity == Severity.high
        assert case.category == Category.fraud

        event_types = [
            e.event_type
            for e in db.query(CaseEvent)
            .filter(CaseEvent.case_id == case_id)
            .order_by(CaseEvent.id)
            .all()
        ]
        assert event_types == [
            "classification_started",
            "ai_classification_succeeded",
            "routed",
        ]
        started = (
            db.query(CaseEvent)
            .filter(
                CaseEvent.case_id == case_id,
                CaseEvent.event_type == "classification_started",
            )
            .one()
        )
        assert started.from_state == CaseState.in_review
        assert started.to_state == CaseState.pending_classification
        assert started.notes == "Retriggered by agent"
        db.close()
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


def test_retrigger_success_with_critical_severity_ends_escalated():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case()

        _retrigger(case_id, _ai_success(Severity.critical, Category.fraud))

        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        assert case.state == CaseState.escalated
        assert case.queue == Queue.fraud
        db.close()
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


def test_retrigger_failing_again_returns_case_to_manual_triage():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case()

        _retrigger(case_id, _ai_failure())

        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        assert case.state == CaseState.in_review
        assert case.queue == Queue.manual_triage

        failed = (
            db.query(CaseEvent)
            .filter(
                CaseEvent.case_id == case_id,
                CaseEvent.event_type == "ai_classification_failed",
            )
            .one()
        )
        assert failed.from_state == CaseState.pending_classification
        assert failed.to_state == CaseState.in_review

        attempts = (
            db.query(AiClassification)
            .filter(AiClassification.case_id == case_id)
            .all()
        )
        assert len(attempts) == 1
        assert attempts[0].succeeded is False
        db.close()
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


@pytest.mark.parametrize(
    "state, queue",
    [
        (CaseState.in_review, Queue.fraud),
        (CaseState.routed, Queue.fraud),
        (CaseState.routed, Queue.manual_triage),
        (CaseState.escalated, Queue.fraud),
        (CaseState.pending_classification, Queue.manual_triage),
        (CaseState.resolved, Queue.manual_triage),
        (CaseState.new, None),
    ],
)
def test_retrigger_rejects_ineligible_cases(state, queue):
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_case(state=state, queue=queue)

        response, mock_classifier = _retrigger(case_id, _ai_success())

        assert response.status_code == 409
        mock_classifier.assert_not_called()

        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        assert case.state == state
        assert (
            db.query(CaseEvent).filter(CaseEvent.case_id == case_id).all() == []
        )
        db.close()
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


def test_retrigger_404_for_missing_case():
    response = client.post("/cases/999999/classify", headers=AGENT)
    assert response.status_code == 404


def test_retrigger_without_role_header_fails():
    response = client.post("/cases/1/classify")
    assert response.status_code == 422


def test_retrigger_as_reporter_forbidden():
    response = client.post("/cases/1/classify", headers={"X-Role": "reporter"})
    assert response.status_code == 403
