from fastapi import FastAPI, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Case, CaseState, Queue
from app.schemas import CaseResponse, CaseCreate, CaseSubmissionConfirmation, ManualClassificationRequest, EscalateRequest, ResolveRequest, CaseEventResponse
from datetime import datetime, timezone
from typing import Optional
from fastapi import HTTPException
from app.auth import require_agent, Role
from fastapi import BackgroundTasks
from app.database import SessionLocal
from app.services.ai_classifier import run_classification
from app.services.case_transitions import transition_case
from app.services.case_router import route_case
from app.services.case_queries import list_cases, states_entered_at, to_case_response, list_case_events
from app.services.case_escalator import auto_escalate_if_critical, escalate_case
from app.services.case_resolver import resolve_case, start_review
from app.state_machine import InvalidTransitionError






app = FastAPI()

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")

@app.get("/cases", response_model=list[CaseResponse])
def get_cases(
    queue: Optional[Queue] = None,
    state: Optional[CaseState] = None,
    db: Session = Depends(get_db),
    role: Role = Depends(require_agent),
):
    cases = list_cases(db, queue=queue, state=state)
    entered = states_entered_at(db, cases)
    now = datetime.now(timezone.utc)
    return [to_case_response(c, entered.get(c.id), now) for c in cases]


@app.get("/cases/{case_id}", response_model=CaseResponse)
def get_case(
    case_id: int, db: Session = Depends(get_db), role: Role = Depends(require_agent)
):
    case = db.query(Case).filter(Case.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@app.post("/cases", response_model=CaseSubmissionConfirmation)
def create_case(
    case: CaseCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    new_case = Case(
        description=case.description,
        reported_entity_type=case.reported_entity_type,
        reported_entity_id=case.reported_entity_id,
        reporter_id=case.reporter_id,
        state=CaseState.new,
    )
    db.add(new_case)
    db.commit()
    db.refresh(new_case)

    background_tasks.add_task(classify_case_background, new_case.id)

    return new_case


def classify_case_background(case_id: int):
    db = SessionLocal()
    try:
        case = db.query(Case).filter(Case.id == case_id).first()
        if case is None:
            return
        transition_case(
            db,
            case,
            CaseState.pending_classification,
            event_type="classification_started",
        )
        run_classification(db, case)
    finally:
        db.close()


@app.post("/cases/{case_id}/classify-manually", response_model=CaseResponse)
def classify_case_manually(
    case_id: int,
    payload: ManualClassificationRequest,
    db: Session = Depends(get_db),
    role: Role = Depends(require_agent),
):
    case = db.query(Case).filter(Case.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")

    if not is_in_manual_triage(case):
        queue_label = case.queue.value if case.queue else None
        raise HTTPException(
            status_code=409,
            detail=(
                f"Case in state '{case.state.value}' with queue '{queue_label}' "
                "is not eligible for manual classification"
            ),
        )

    case.severity = payload.severity
    case.category = payload.category
    db.add(case)
    db.commit()
    db.refresh(case)

    transition_case(db, case, CaseState.classified, event_type="manually_classified")
    case = route_case(db, case)
    case = auto_escalate_if_critical(db, case)

    return case


def is_in_manual_triage(case: Case) -> bool:
    return case.state == CaseState.in_review and case.queue == Queue.manual_triage


def retrigger_classification_background(case_id: int):
    db = SessionLocal()
    try:
        case = db.query(Case).filter(Case.id == case_id).first()
        if case is None:
            return
        run_classification(db, case)
    finally:
        db.close()


@app.post("/cases/{case_id}/classify", response_model=CaseResponse)
def retrigger_classification(
    case_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    role: Role = Depends(require_agent),
):
    case = db.query(Case).filter(Case.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")

    if not is_in_manual_triage(case):
        queue_label = case.queue.value if case.queue else None
        raise HTTPException(
            status_code=409,
            detail=(
                f"Case in state '{case.state.value}' with queue '{queue_label}' "
                "is not eligible for AI reclassification"
            ),
        )

    case = transition_case(
        db,
        case,
        CaseState.pending_classification,
        event_type="classification_started",
        notes="Retriggered by agent",
    )
    background_tasks.add_task(retrigger_classification_background, case.id)
    return case

@app.post("/cases/{case_id}/escalate", response_model=CaseResponse)
def escalate_case_endpoint(
    case_id: int,
    payload: EscalateRequest,
    db: Session = Depends(get_db),
    role: Role = Depends(require_agent),
):
    case = db.query(Case).filter(Case.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")

    try:
        return escalate_case(db, case, reason=f"Manually escalated: {payload.reason}")
    except InvalidTransitionError:
        raise HTTPException(
            status_code=409,
            detail=f"Case in state '{case.state.value}' cannot be escalated",
        )


@app.post("/cases/{case_id}/resolve", response_model=CaseResponse)
def resolve_case_endpoint(
    case_id: int,
    payload: ResolveRequest,
    db: Session = Depends(get_db),
    role: Role = Depends(require_agent),
):
    case = db.query(Case).filter(Case.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")

    try:
        return resolve_case(db, case, resolution=payload.resolution)
    except InvalidTransitionError:
        raise HTTPException(
            status_code=409,
            detail=f"Case in state '{case.state.value}' cannot be resolved",
        )


@app.post("/cases/{case_id}/pickup", response_model=CaseResponse)
def pickup_case_endpoint(
    case_id: int,
    db: Session = Depends(get_db),
    role: Role = Depends(require_agent),
):
    case = db.query(Case).filter(Case.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")

    try:
        return start_review(db, case)
    except InvalidTransitionError:
        raise HTTPException(
            status_code=409,
            detail=f"Case in state '{case.state.value}' cannot be picked up",
        )


@app.get("/cases/{case_id}/events", response_model=list[CaseEventResponse])
def get_case_events(
    case_id: int,
    db: Session = Depends(get_db),
    role: Role = Depends(require_agent),
):
    case = db.query(Case).filter(Case.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return list_case_events(db, case_id)
