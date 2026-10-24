from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import (
    AiClassification,
    Case,
    CaseEvent,
    Category,
    Severity,
)
from tests.conftest import (
    TestSessionLocal,
    override_get_db,
    override_get_db_committing,
)

client = TestClient(app)
AGENT = {"X-Role": "agent"}

CLASSIFIER = "app.services.ai_classifier.classify_case_description"


def _cleanup_case(case_id):
    if case_id is None:
        return
    db = TestSessionLocal()
    db.query(AiClassification).filter(AiClassification.case_id == case_id).delete()
    db.query(CaseEvent).filter(CaseEvent.case_id == case_id).delete()
    db.query(Case).filter(Case.id == case_id).delete()
    db.commit()
    db.close()


def test_case_lifecycle_from_report_to_resolution_tells_the_right_story():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        ai_result = SimpleNamespace(
            severity=Severity.medium,
            category=Category.fraud,
            confidence=0.9,
            raw_response="{}",
            succeeded=True,
        )
        with patch(CLASSIFIER, return_value=ai_result), patch(
            "app.main.SessionLocal", TestSessionLocal
        ):
            response = client.post(
                "/cases",
                json={
                    "description": "Seller asked me to pay outside the app",
                    "reported_entity_type": "user",
                    "reported_entity_id": 42,
                },
            )
        assert response.status_code == 200
        case_id = response.json()["id"]

        case = client.get(f"/cases/{case_id}", headers=AGENT).json()
        assert case["state"] == "routed"
        assert case["queue"] == "fraud"
        assert case["severity"] == "medium"
        assert case["category"] == "fraud"

        response = client.post(f"/cases/{case_id}/pickup", headers=AGENT)
        assert response.status_code == 200
        assert response.json()["state"] == "in_review"

        response = client.post(
            f"/cases/{case_id}/resolve",
            headers=AGENT,
            json={"resolution": "Account suspended for off-platform payments"},
        )
        assert response.status_code == 200
        assert response.json()["state"] == "resolved"

        response = client.get(f"/cases/{case_id}/events", headers=AGENT)
        assert response.status_code == 200
        events = response.json()

        assert [e["event_type"] for e in events] == [
            "classification_started",
            "ai_classification_succeeded",
            "routed",
            "review_started",
            "resolved",
        ]
        assert [(e["from_state"], e["to_state"]) for e in events] == [
            ("new", "pending_classification"),
            ("pending_classification", "classified"),
            ("classified", "routed"),
            ("routed", "in_review"),
            ("in_review", "resolved"),
        ]
        assert "fraud" in events[2]["notes"]
        assert events[4]["notes"] == "Account suspended for off-platform payments"

        for previous, current in zip(events, events[1:]):
            assert current["from_state"] == previous["to_state"]

        db = TestSessionLocal()
        attempts = (
            db.query(AiClassification)
            .filter(AiClassification.case_id == case_id)
            .all()
        )
        db.close()
        assert len(attempts) == 1
        assert attempts[0].succeeded is True

        listed_ids = [c["id"] for c in client.get("/cases", headers=AGENT).json()]
        assert case_id not in listed_ids
        assert client.get(f"/cases/{case_id}", headers=AGENT).status_code == 200
        assert client.post(f"/cases/{case_id}/pickup", headers=AGENT).status_code == 409
        assert (
            client.post(
                f"/cases/{case_id}/resolve",
                headers=AGENT,
                json={"resolution": "again"},
            ).status_code
            == 409
        )
    finally:
        _cleanup_case(case_id)
        app.dependency_overrides[get_db] = override_get_db
