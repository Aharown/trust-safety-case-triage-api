import pytest
from tests.conftest import TestSessionLocal
from app.models import Case, CaseEvent, CaseState, ReportedEntityType, Category, Queue
from app.services.case_router import assign_queue, route_case, UnroutableCaseError
from app.state_machine import InvalidTransitionError


def _make_case(state, category=None):
    db = TestSessionLocal()
    case = Case(
        description="test",
        reported_entity_type=ReportedEntityType.listing,
        reported_entity_id=1,
        state=state,
        category=category,
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    case_id = case.id
    db.close()
    return case_id


def _cleanup_case(case_id):
    if case_id is None:
        return
    db = TestSessionLocal()
    db.query(CaseEvent).filter(CaseEvent.case_id == case_id).delete()
    db.query(Case).filter(Case.id == case_id).delete()
    db.commit()
    db.close()


@pytest.mark.parametrize(
    "category,expected_queue",
    [
        (Category.fraud, Queue.fraud),
        (Category.prohibited_item, Queue.prohibited_items),
        (Category.community_guideline, Queue.community_guidelines),
        (Category.other, Queue.general),
    ],
)
def test_assign_queue_maps_each_category(category, expected_queue):
    case_id = _make_case(CaseState.classified, category=category)
    try:
        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        assert assign_queue(case) == expected_queue
        db.close()
    finally:
        _cleanup_case(case_id)


def test_assign_queue_raises_when_category_missing():
    case_id = _make_case(CaseState.classified, category=None)
    try:
        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        with pytest.raises(UnroutableCaseError):
            assign_queue(case)
        db.close()
    finally:
        _cleanup_case(case_id)


def test_route_case_sets_queue_and_transitions_to_routed():
    case_id = _make_case(CaseState.classified, category=Category.fraud)
    try:
        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        result = route_case(db, case)
        assert result.state == CaseState.routed
        assert result.queue == Queue.fraud
        db.close()
    finally:
        _cleanup_case(case_id)


def test_route_case_writes_case_event():
    case_id = _make_case(CaseState.classified, category=Category.community_guideline)
    try:
        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        route_case(db, case)

        event = (
            db.query(CaseEvent)
            .filter(CaseEvent.case_id == case_id)
            .order_by(CaseEvent.id.desc())
            .first()
        )
        assert event is not None
        assert event.event_type == "routed"
        assert event.from_state == CaseState.classified
        assert event.to_state == CaseState.routed
        db.close()
    finally:
        _cleanup_case(case_id)


def test_route_case_rejects_non_classified_case():
    case_id = _make_case(CaseState.new, category=Category.fraud)
    try:
        db = TestSessionLocal()
        case = db.query(Case).filter(Case.id == case_id).first()
        with pytest.raises(InvalidTransitionError):
            route_case(db, case)
        db.close()
    finally:
        _cleanup_case(case_id)
