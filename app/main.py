from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Case, CaseState
from app.schemas import CaseResponse, CaseCreate

app = FastAPI()


@app.get("/cases", response_model=list[CaseResponse])
def get_cases(db: Session = Depends(get_db)):
    return db.query(Case).all()


@app.post("/cases", response_model=CaseResponse)
def create_case(case: CaseCreate, db: Session = Depends(get_db)):
    new_case = Case(description=case.description, state=CaseState.new)
    db.add(new_case)
    db.commit()
    db.refresh(new_case)
    return new_case
