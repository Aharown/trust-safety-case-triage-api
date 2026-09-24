from unittest.mock import patch
from app.database import get_db
from tests.conftest import override_get_db_committing, override_get_db
from fastapi.testclient import TestClient
from tests.conftest import TestSessionLocal
from app.main import app
from app.models import (
    Case,
    CaseEvent,
    CaseState,
    ReportedEntityType,
    Severity,
    Category,
    AiClassification,
)
import pytest

client = TestClient(app)


def _make_manual_triage_case():
    db = TestSessionLocal()
    case = Case(
        description="test",
        reported_entity_type=ReportedEntityType.listing,
        reported_entity_id=1,
        state=CaseState.in_review,
        queue="manual_triage",
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
    cleanup = TestSessionLocal()
    cleanup.query(AiClassification).filter(
        AiClassification.case_id == case_id
    ).delete()
    cleanup.query(CaseEvent).filter(CaseEvent.case_id == case_id).delete()
    cleanup.query(Case).filter(Case.id == case_id).delete()
    cleanup.commit()
    cleanup.close()


def test_classify_case_manually_success():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_manual_triage_case()

        response = client.post(
            f"/cases/{case_id}/classify-manually",
            json={"severity": "high", "category": "fraud"},
            headers={"X-Role": "agent"},
        )

        assert response.status_code == 200

        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        assert case.state == CaseState.classified
        assert case.severity == Severity.high
        assert case.category == Category.fraud
        assert case.queue == "manual_triage"  # unchanged, CaseRouter not built yet
        db.close()
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


def test_classify_case_manually_creates_case_event():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_manual_triage_case()

        client.post(
            f"/cases/{case_id}/classify-manually",
            json={"severity": "low", "category": "other"},
            headers={"X-Role": "agent"},
        )

        db = TestSessionLocal()
        event = (
            db.query(CaseEvent)
            .filter(CaseEvent.case_id == case_id)
            .order_by(CaseEvent.id.desc())
            .first()
        )
        assert event is not None
        assert event.event_type == "manually_classified"
        assert event.from_state == CaseState.in_review
        assert event.to_state == CaseState.classified
        db.close()
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


def test_classify_case_manually_does_not_create_ai_classification():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_manual_triage_case()

        response = client.post(
            f"/cases/{case_id}/classify-manually",
            json={"severity": "medium", "category": "prohibited_item"},
            headers={"X-Role": "agent"},
        )

        assert response.status_code == 200

        db = TestSessionLocal()
        ai_rows = (
            db.query(AiClassification)
            .filter(AiClassification.case_id == case_id)
            .all()
        )
        assert ai_rows == []
        db.close()
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


@pytest.mark.parametrize(
    "state,queue",
    [
        (CaseState.new, None),
        (CaseState.in_review, None),
        (CaseState.in_review, "billing_team"),
        (CaseState.classified, "manual_triage"),
        (CaseState.resolved, "manual_triage"),
    ],
)
def test_classify_case_manually_rejects_ineligible_states(state, queue):
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
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

        response = client.post(
            f"/cases/{case_id}/classify-manually",
            json={"severity": "high", "category": "fraud"},
            headers={"X-Role": "agent"},
        )

        assert response.status_code == 409
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)


def test_classify_case_manually_404_for_missing_case():
    response = client.post(
        "/cases/999999/classify-manually",
        json={"severity": "high", "category": "fraud"},
        headers={"X-Role": "agent"},
    )
    assert response.status_code == 404


def test_classify_case_manually_without_role_header_fails():
    response = client.post(
        "/cases/1/classify-manually",
        json={"severity": "high", "category": "fraud"},
    )
    assert response.status_code == 422


def test_classify_case_manually_as_reporter_forbidden():
    response = client.post(
        "/cases/1/classify-manually",
        json={"severity": "high", "category": "fraud"},
        headers={"X-Role": "reporter"},
    )
    assert response.status_code == 403


def test_classify_case_manually_as_agent_allowed():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        case_id = _make_manual_triage_case()

        response = client.post(
            f"/cases/{case_id}/classify-manually",
            json={"severity": "high", "category": "fraud"},
            headers={"X-Role": "agent"},
        )

        assert response.status_code == 200
    finally:
        app.dependency_overrides[get_db] = override_get_db
        _cleanup_case(case_id)
