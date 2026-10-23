import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import AiClassification, Case, CaseEvent, Category, Severity
from app.services.ai_classifier import classify_case_description
from tests.conftest import (
    TestSessionLocal,
    override_get_db,
    override_get_db_committing,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_AI_TESTS") != "1",
    reason="Calls the real Claude API. Set RUN_LIVE_AI_TESTS=1 to run.",
)

client = TestClient(app)
AGENT = {"X-Role": "agent"}

SCAM_REPORT = (
    "A seller took 85 pounds from me for a designer bag, never posted it, "
    "and has now blocked me."
)


def _cleanup_case(case_id):
    if case_id is None:
        return
    db = TestSessionLocal()
    db.query(AiClassification).filter(AiClassification.case_id == case_id).delete()
    db.query(CaseEvent).filter(CaseEvent.case_id == case_id).delete()
    db.query(Case).filter(Case.id == case_id).delete()
    db.commit()
    db.close()


def test_real_claude_returns_a_usable_classification():
    result = classify_case_description(SCAM_REPORT)

    assert result.succeeded, f"Claude call failed: {result.raw_response}"

    assert isinstance(result.severity, Severity)
    assert isinstance(result.category, Category)
    assert 0 <= result.confidence <= 1
    assert result.raw_response

    assert result.category == Category.fraud


def test_full_lifecycle_with_the_real_claude_api():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        with patch("app.main.SessionLocal", TestSessionLocal):
            response = client.post(
                "/cases",
                json={
                    "description": SCAM_REPORT,
                    "reported_entity_type": "user",
                    "reported_entity_id": 42,
                },
            )
        assert response.status_code == 200
        case_id = response.json()["id"]

        case = client.get(f"/cases/{case_id}", headers=AGENT).json()
        if case["queue"] == "manual_triage":
            db = TestSessionLocal()
            attempt = (
                db.query(AiClassification)
                .filter(AiClassification.case_id == case_id)
                .first()
            )
            db.close()
            detail = attempt.raw_response if attempt else "no AiClassification row"
            pytest.fail(f"The real AI call failed, case fell to manual triage: {detail}")

        assert case["state"] in ("routed", "escalated")
        assert case["severity"] in {s.value for s in Severity}
        assert case["category"] in {c.value for c in Category}
        assert case["queue"] is not None

        response = client.post(f"/cases/{case_id}/pickup", headers=AGENT)
        assert response.status_code == 200
        assert response.json()["state"] == "in_review"

        response = client.post(
            f"/cases/{case_id}/resolve",
            headers=AGENT,
            json={"resolution": "Live test resolution"},
        )
        assert response.status_code == 200
        assert response.json()["state"] == "resolved"

        events = client.get(f"/cases/{case_id}/events", headers=AGENT).json()
        types = [e["event_type"] for e in events]

        assert types[:3] == [
            "classification_started",
            "ai_classification_succeeded",
            "routed",
        ]
        assert types[-2:] == ["review_started", "resolved"]
        middle = events[3:-2]
        assert len(middle) <= 1
        if middle:
            assert middle[0]["to_state"] == "escalated"

        assert case["queue"] in events[2]["notes"]
        assert events[-1]["notes"] == "Live test resolution"

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
        assert 0 <= attempts[0].confidence_score <= 1
    finally:
        _cleanup_case(case_id)
        app.dependency_overrides[get_db] = override_get_db
