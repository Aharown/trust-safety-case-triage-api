from dataclasses import dataclass
from app.ai_client import client
from app.models import Severity, Category
from sqlalchemy.orm import Session
from app.models import Case, AiClassification, CaseState
from app.services.case_transitions import transition_case


CLASSIFY_TOOL = {
    "name": "classify_case",
    "description": "Classify a trust and safety case by severity and category",
    "input_schema": {
        "type": "object",
        "properties": {
            "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            "category": {"type": "string", "enum": ["fraud", "prohibited_item", "community_guideline", "other"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1}
        },
        "required": ["severity", "category", "confidence"]
    }
}


@dataclass
class ClassificationResult:
    succeeded: bool
    severity: Severity | None = None
    category: Category | None = None
    confidence: float | None = None
    raw_response: str | None = None


def classify_case_description(description: str) -> ClassificationResult:
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            tools=[CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "classify_case"},
            messages=[{"role": "user", "content": f"Classify this trust and safety case: {description}"}]
        )
    except Exception as e:
        return ClassificationResult(succeeded=False, raw_response=str(e))

    raw_response_text = str(response.content)

    tool_use_block = next((block for block in response.content if block.type == "tool_use"), None)
    if tool_use_block is None:
        return ClassificationResult(succeeded=False, raw_response=raw_response_text)

    try:
        input_data = tool_use_block.input
        severity = Severity(input_data["severity"])
        category = Category(input_data["category"])
        confidence = float(input_data["confidence"])
    except (KeyError, ValueError) as e:
        return ClassificationResult(succeeded=False, raw_response=raw_response_text)

    return ClassificationResult(
        succeeded=True,
        severity=severity,
        category=category,
        confidence=confidence,
        raw_response=raw_response_text,
    )


def run_classification(db: Session, case: Case) -> AiClassification:
    result = classify_case_description(case.description)

    classification = AiClassification(
        case_id=case.id,
        suggested_severity=result.severity,
        suggested_category=result.category,
        confidence_score=result.confidence,
        raw_response=result.raw_response,
        succeeded=result.succeeded,
    )
    db.add(classification)
    db.commit()
    db.refresh(classification)

    if result.succeeded:
        case.severity = result.severity
        case.category = result.category
        case.ai_confidence_score = result.confidence
        db.add(case)
        db.commit()
        db.refresh(case)
        transition_case(db, case, CaseState.classified, event_type="ai_classification_succeeded")
    else:
        transition_case(db, case, CaseState.new, event_type="ai_classification_failed")

    return classification
