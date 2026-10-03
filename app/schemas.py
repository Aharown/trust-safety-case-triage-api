from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional
from app.models import Severity, Category, CaseState, ReportedEntityType
from pydantic import BaseModel, ConfigDict, Field


class CaseSubmissionConfirmation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int


class CaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    description: str
    reporter_id: Optional[int]
    reported_entity_type: ReportedEntityType
    reported_entity_id: int
    severity: Optional[Severity]
    category: Optional[Category]
    state: CaseState
    queue: Optional[str]
    ai_confidence_score: Optional[float]
    time_in_state_hours: Optional[float] = None
    created_at: datetime
    updated_at: datetime


class CaseCreate(BaseModel):
    description: str
    reporter_id: Optional[int] = None
    reported_entity_type: ReportedEntityType
    reported_entity_id: int


class ManualClassificationRequest(BaseModel):
    severity: Severity
    category: Category


class EscalateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(min_length=1)
