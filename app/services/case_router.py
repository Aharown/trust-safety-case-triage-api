from sqlalchemy.orm import Session

from app.models import Case, CaseState, Category, Queue
from app.services.case_transitions import transition_case

CATEGORY_TO_QUEUE: dict[Category, Queue] = {
    Category.fraud: Queue.fraud,
    Category.prohibited_item: Queue.prohibited_items,
    Category.community_guideline: Queue.community_guidelines,
    Category.other: Queue.general,
}


class UnroutableCaseError(Exception):
    pass


def assign_queue(case: Case) -> Queue:
    if case.category is None:
        raise UnroutableCaseError(
            f"Case {case.id} has no category set. Cannot assign a queue."
        )
    try:
        return CATEGORY_TO_QUEUE[case.category]
    except KeyError:
        raise UnroutableCaseError(
            f"Case {case.id} has category {case.category!r}, which has no queue mapping."
        )


def route_case(db: Session, case: Case) -> Case:
    queue = assign_queue(case)
    case.queue = queue

    return transition_case(
        db,
        case,
        to_state=CaseState.routed,
        event_type="routed",
        notes=f"Assigned to {queue.value} based on category={case.category.value}.",
    )
