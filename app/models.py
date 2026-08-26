import enum
from sqlalchemy import Column, Integer, String, Text, Float, DateTime, ForeignKey, Enum
from sqlalchemy.sql import func
from app.database import Base


class Severity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Category(str, enum.Enum):
    fraud = "fraud"
    prohibited_item = "prohibited_item"
    community_guideline = "community_guideline"
    other = "other"


class CaseState(str, enum.Enum):
    new = "new"
    pending_classification = "pending_classification"
    classified = "classified"
    routed = "routed"
    in_review = "in_review"
    escalated = "escalated"
    resolved = "resolved"
    reopened = "reopened"


class Case(Base):
    __tablename__ = "cases"

    id = Column(Integer, primary_key=True, index=True)
    description = Column(Text, nullable=False)
    severity = Column(Enum(Severity), nullable=True)
    category = Column(Enum(Category), nullable=True)
    state = Column(Enum(CaseState), nullable=False, default=CaseState.new)
    queue = Column(String, nullable=True)
    ai_confidence_score = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    

class CaseEvent(Base):
    __tablename__ = "case_events"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    event_type = Column(String, nullable=False)
    from_state = Column(Enum(CaseState), nullable=True)
    to_state = Column(Enum(CaseState), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
