from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Case, CaseState
from app.schemas import CaseResponse, CaseCreate, CaseSubmissionConfirmation
from fastapi import HTTPException
from app.auth import require_agent, Role
from fastapi import BackgroundTasks
from app.database import SessionLocal
from app.services.ai_classifier import run_classification
from app.services.case_transitions import transition_case

app = FastAPI()


@app.get("/cases", response_model=list[CaseResponse])
def get_cases(db: Session = Depends(get_db), role: Role = Depends(require_agent)):
    return db.query(Case).all()


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
