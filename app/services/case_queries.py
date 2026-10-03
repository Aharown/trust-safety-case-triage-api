from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import case as sql_case, func
from sqlalchemy.orm import Session

from app.models import Case, CaseEvent, CaseState, Queue, Severity
from app.schemas import CaseResponse

SEVERITY_RANK = sql_case(
    {
        Severity.critical: 0,
        Severity.high: 1,
        Severity.medium: 2,
        Severity.low: 3,
    },
    value=Case.severity,
    else_=4,
)

ESCALATED_FIRST = sql_case((Case.state == CaseState.escalated, 0), else_=1)


def list_cases(
    db: Session,
    queue: Optional[Queue] = None,
    state: Optional[CaseState] = None,
) -> list[Case]:
    query = db.query(Case)
    if queue is not None:
        query = query.filter(Case.queue == queue)
    if state is not None:
        query = query.filter(Case.state == state)
    return query.order_by(
        ESCALATED_FIRST, SEVERITY_RANK, Case.created_at.asc(), Case.id.asc()
    ).all()


def states_entered_at(db: Session, cases: list[Case]) -> dict[int, datetime]:
    if not cases:
        return {}
    rows = (
        db.query(CaseEvent.case_id, func.max(CaseEvent.created_at))
        .join(Case, Case.id == CaseEvent.case_id)
        .filter(
            CaseEvent.case_id.in_([c.id for c in cases]),
            CaseEvent.to_state == Case.state,
        )
        .group_by(CaseEvent.case_id)
        .all()
    )
    return dict(rows)


def time_in_state_hours(case: Case, entered_at, now: datetime) -> float:
    since = entered_at or case.created_at
    return round(max(0.0, (now - since).total_seconds() / 3600), 1)


def to_case_response(case: Case, entered_at, now: datetime) -> CaseResponse:
    response = CaseResponse.model_validate(case)
    return response.model_copy(
        update={"time_in_state_hours": time_in_state_hours(case, entered_at, now)}
    )
