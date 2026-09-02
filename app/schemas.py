from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional
from app.models import Severity, Category, CaseState, ReportedEntityType


class CaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reporter_id: Optional[int]
    reported_entity_type: ReportedEntityType
    reported_entity_id: int
    description: str
    severity: Optional[Severity]
    category: Optional[Category]
    state: CaseState
    queue: Optional[str]
    ai_confidence_score: Optional[float]
    created_at: datetime
    updated_at: datetime


class CaseCreate(BaseModel):
    description: str
    reporter_id: Optional[int] = None
    reported_entity_type: ReportedEntityType
    reported_entity_id: int
