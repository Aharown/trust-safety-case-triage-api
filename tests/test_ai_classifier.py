from unittest.mock import patch, MagicMock
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
    AiClassification,
    Category,
)
from app.services.ai_classifier import (
    run_classification,
    classify_case_description,
    ClassificationResult,
)
import pytest


def test_classify_case_description_parses_successful_tool_response():
    fake_tool_block = MagicMock()
    fake_tool_block.type = "tool_use"
    fake_tool_block.input = {
        "severity": "high",
        "category": "fraud",
        "confidence": 0.87,
    }

    fake_response = MagicMock()
    fake_response.content = [fake_tool_block]

    with patch(
        "app.services.ai_classifier.client.messages.create", return_value=fake_response
    ):
        result = classify_case_description("Seller sent a counterfeit item")

    assert result.succeeded is True
    assert result.severity == Severity.high
    assert result.category == Category.fraud
    assert result.confidence == 0.87
    assert result.raw_response is not None


def test_run_classification_writes_full_ai_classification_row(db):
    case = Case(
        description="Suspicious high-value listing",
        reported_entity_type=ReportedEntityType.listing,
        reported_entity_id=1,
        state=CaseState.pending_classification,
    )
    db.add(case)
    db.commit()
    db.refresh(case)

    fake_result = ClassificationResult(
        succeeded=True,
        severity=Severity.critical,
        category=Category.fraud,
        confidence=0.95,
        raw_response="mocked response text",
    )

    with patch(
        "app.services.ai_classifier.classify_case_description", return_value=fake_result
    ):
        classification = run_classification(db, case)

    assert classification.case_id == case.id
    assert classification.suggested_severity == Severity.critical
    assert classification.suggested_category == Category.fraud
    assert classification.confidence_score == 0.95
    assert classification.raw_response == "mocked response text"
    assert classification.succeeded is True

    assert case.severity == Severity.critical
    assert case.category == Category.fraud
    assert case.ai_confidence_score == 0.95


def test_run_classification_success_creates_case_event(db):
    case = Case(
        description="test",
        reported_entity_type=ReportedEntityType.listing,
        reported_entity_id=1,
        state=CaseState.pending_classification,
    )
    db.add(case)
    db.commit()
    db.refresh(case)

    fake_result = ClassificationResult(
        succeeded=True,
        severity=Severity.low,
        category=Category.other,
        confidence=0.6,
        raw_response="mocked",
    )

    with patch(
        "app.services.ai_classifier.classify_case_description", return_value=fake_result
    ):
        run_classification(db, case)

    event = db.query(CaseEvent).filter(CaseEvent.case_id == case.id).first()
    assert event is not None
    assert event.event_type == "ai_classification_succeeded"
    assert event.from_state == CaseState.pending_classification
    assert event.to_state == CaseState.classified


def test_run_classification_success(db):
    case = Case(
        description="Seller sent counterfeit item",
        reported_entity_type=ReportedEntityType.listing,
        reported_entity_id=1,
        state=CaseState.pending_classification,
    )
    db.add(case)
    db.commit()
    db.refresh(case)

    fake_result = ClassificationResult(
        succeeded=True,
        severity=Severity.high,
        category=Category.fraud,
        confidence=0.9,
        raw_response="mocked",
    )

    with patch(
        "app.services.ai_classifier.classify_case_description", return_value=fake_result
    ):
        classification = run_classification(db, case)

    assert classification.succeeded is True
    assert case.state == CaseState.classified
    assert case.severity == Severity.high


def test_run_classification_failure_falls_back_to_new(db):
    case = Case(
        description="test",
        reported_entity_type=ReportedEntityType.listing,
        reported_entity_id=1,
        state=CaseState.pending_classification,
    )
    db.add(case)
    db.commit()
    db.refresh(case)

    fake_result = ClassificationResult(succeeded=False, raw_response=None)

    with patch(
        "app.services.ai_classifier.classify_case_description", return_value=fake_result
    ):
        classification = run_classification(db, case)

    assert classification.succeeded is False
    assert case.state == CaseState.new
    assert case.severity is None


@pytest.mark.integration
def test_classify_case_description_real_api_call():
    result = classify_case_description(
        "Seller sent a counterfeit designer bag and is refusing a refund"
    )
    assert result.succeeded is True
    assert result.severity is not None


client = TestClient(app)


def test_create_case_triggers_classification():
    app.dependency_overrides[get_db] = override_get_db_committing
    case_id = None
    try:
        fake_result = ClassificationResult(
            succeeded=True,
            severity=Severity.high,
            category=Category.fraud,
            confidence=0.9,
            raw_response="mocked",
        )

        with patch(
            "app.services.ai_classifier.classify_case_description",
            return_value=fake_result,
        ), patch("app.main.SessionLocal", TestSessionLocal):
            response = client.post(
                "/cases",
                json={
                    "description": "Seller sent a counterfeit item",
                    "reported_entity_type": "listing",
                    "reported_entity_id": 1,
                },
            )

        assert response.status_code == 200
        case_id = response.json()["id"]

        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        assert case is not None
        assert case.state == CaseState.classified
        db.close()
    finally:
        app.dependency_overrides[get_db] = override_get_db
        if case_id is not None:
            cleanup = TestSessionLocal()
            cleanup.query(AiClassification).filter(
                AiClassification.case_id == case_id
            ).delete()
            cleanup.query(CaseEvent).filter(CaseEvent.case_id == case_id).delete()
            cleanup.query(Case).filter(Case.id == case_id).delete()
            cleanup.commit()
            cleanup.close()
