# Trust & Safety Case Triage API, technical spec

---

## Data model

### Case

The central record. One case per reported issue.

| Field | Type | Notes |
|---|---|---|
| id | int | primary key |
| reporter_id | int, nullable | lightweight id for who filed the case, not a foreign key to a full user model, which is out of scope for this project |
| reported_entity_type | enum | listing, user, transaction, other |
| reported_entity_id | int | the id of the specific entity being reported, meaning depends on entity_type |
| description | text | the reported issue, what gets sent to AI classification |
| severity | enum, nullable | low, medium, high, critical. Set via AI suggestion or manual classification, null until classified |
| category | enum, nullable | fraud, prohibited_item, community_guideline, other. Null until classified |
| state | enum | see State machine below |
| queue | enum, nullable | "Queue": fraud, prohibited_items, community_guidelines, general, manual_triage. Null until routed or failed into manual triage |
| ai_confidence_score | float | how confident the AI classification was, nullable until classified |
| created_at | datetime | |
| updated_at | datetime | |

### CaseEvent

An audit log entry, one row per state transition or significant action on a case. Exists so a case's history is reconstructable, not just its current state. It is also the source of truth for whether a case has ever been escalated and when a case entered its current state.

| Field | Type | Notes |
|---|---|---|
| id | int | primary key |
| case_id | int | foreign key to Case |
| event_type | string | what caused the transition. Currently: "classification_started", "ai_classification_succeeded", "ai_classification_failed", "manually_classified", "routed", "escalated" |
| from_state | enum | nullable, the state before this event |
| to_state | enum | nullable, the state after this event |
| notes | text | nullable, for example the routing reason or the escalation reason |
| created_at | datetime | |

### AiClassification

A record of one AI classification attempt on a case. Kept separate from Case itself so classification history is preserved even if a case gets reclassified.

| Field | Type | Notes |
|---|---|---|
| id | int | primary key |
| case_id | int | foreign key to Case |
| suggested_severity | enum | |
| suggested_category | enum | |
| confidence_score | float | |
| raw_response | text | the full AI response, kept for debugging and audit |
| succeeded | boolean | false if the AI call failed or returned something unusable |
| created_at | datetime | |

---

## State machine

### States

- `new` — case just created, not yet classified
- `pending_classification` — AI classification has been requested, awaiting result
- `classified` — classification complete (AI or manual), awaiting routing
- `routed` — assigned to a queue, awaiting human review
- `in_review` — a human is actively working the case
- `escalated` — flagged for higher-priority handling
- `resolved` — case closed
- `reopened` — a resolved case has been reopened
### Valid transitions

| From | To | Trigger |
|---|---|---|
| new | pending_classification | classification requested |
| pending_classification | classified | AI classification succeeds |
| pending_classification | in_review | On failure, falls back to manual triage queue |
| classified | routed | routing logic assigns a queue |
| routed | escalated | critical severity auto-escalates before pickup, or an agent escalates manually |
| routed | in_review | a reviewer picks up the case |
| in_review | escalated | an agent escalates manually |
| in_review | resolved | reviewer closes the case |
| in_review | pending_classification | agent manually retriggers AI classification on a `manual_triage` case |
| in_review | classified | agent manually classifies a `manual_triage` case, no AI involved |
| escalated | in_review | de-escalated back to normal review |
| escalated | resolved | resolved directly from escalation |
| resolved | reopened | case reopened |
| reopened | in_review | back into active review |

Any transition not listed above is invalid and must be rejected. This table is enforced in one place (`VALID_TRANSITIONS` in `app/state_machine.py`) and every state change goes through `transition_case`, which validates the transition, applies it, and writes the `CaseEvent` in one step.

---

## AI integration

- **Input**: case description
- **Output**: suggested severity, suggested category, confidence score
- **Position**: advisory only. The AI classification informs routing, it does not make the final resolution decision. A human can override at any point.
- **Mechanism**: implemented via Claude's tool-use feature. A tool schema declares `severity`, `category`, and `confidence` with enum constraints matching `Severity` and `Category` exactly, and `tool_choice` forces the model to call it. This was chosen over asking the model to reply with JSON directly, since JSON-prompting relies on the model reliably following formatting instructions (no stray text, correct casing, no trailing commas), while tool-use has the API itself enforce the shape before the response is ever parsed, which eliminates potential parsing bugs rather than just reducing their likelihood.
- **Failure handling, never raises**: `classify_case_description` always returns a `ClassificationResult` object, `succeeded=True` or `succeeded=False`, it never raises an exception for an expected failure mode (timeout, network error, malformed or unparseable response). This matters because classification can be triggered from a background task with no request left to catch an error. An uncaught exception there would silently vanish into the logs with no case update and no audit trail. Only expected external-API failure modes are captured and returned as a result. Callers branch on `result.succeeded` rather than using try/except.
- **Failure outcome**: if the AI call times out or returns something malformed, the case is assigned `Queue.manual_triage` and moved to `in_review`, rather than reverting to `new`. An `AiClassification` row is still written with `succeeded: false` so failures are visible and auditable, not silently dropped. Reverting to `new` was the original design but conflated "not yet looked at" with "AI already tried and failed", which are meaningfully different situations requiring different urgency. Routing failures into a real queue, rather than leaving them in an ambiguous unqueued state, ensures a human is guaranteed to see them.
- **No automatic retry**: a failed classification is not automatically retried. Retry only happens if an agent manually calls `POST /cases/{id}/classify`. This is a deliberate choice, not a gap: a failed classification may reflect a genuinely unclassifiable or ambiguous description rather than a transient API issue, and blind automatic retry risks repeating the same failure indefinitely without surfacing it for human judgement.

---

## Routing

Routing is implemented by `CaseRouter` (`app/services/case_router.py`).

- **Input**: `category` only. Routing does not read `severity`.
- **Mapping**, `Category` to `Queue`, both real enums:
  - `fraud` to `Queue.fraud`
  - `prohibited_item` to `Queue.prohibited_items`
  - `community_guideline` to `Queue.community_guidelines`
  - `other` to `Queue.general`
- **Two functions, two jobs**: `assign_queue(case)` is a pure function, category in and `Queue` out. `route_case(db, case)` sets `case.queue` and calls `transition_case(..., CaseState.routed, event_type="routed")`, which writes the `CaseEvent` with the routing reason in `notes`.
- **Fails loudly**: if `category` is missing or has no mapping, `assign_queue` raises `UnroutableCaseError` rather than defaulting to `general`.
- **`manual_triage` is not a routing outcome.** It is assigned by the classification-failure path in `AiClassifier`, before a case has a category to route on. Therefore a case whose classification failed never goes through `CaseRouter`.
- **Wiring**: routing is not its own endpoint. It fires automatically right after a case reaches `classified`, from both places that produce that state: the success branch of `run_classification` (AI path) and `classify_case_manually` (human path).
- **Automatic escalation check**: immediately after each `route_case` call, `auto_escalate_if_critical` runs.
---

## Escalation

Escalation is implemented by `CaseEscalator` (`app/services/case_escalator.py`).

### What escalated means

`escalated` is a case state. Escalating a case never changes its queue. An escalated case stays in its category's queue and is shown first in that queue's list. A fraud reviewer sees escalated fraud cases ahead of everything else in the fraud queue, and a prohibited-items reviewer does the same for their queue. Queue says who handles a case. State and severity determines urgency.

### Constraint: escalation reads `Case` only

Escalation conditions are read from `Case` fields (and `CaseEvent` history), never from `AiClassification`. Therefore a manually classified case escalates identically to an AI-classified one, with no special-casing.

### Automatic escalation of critical cases

- **Rule**: a case with `severity == critical` that is in `routed` and has never been escalated before is moved to `escalated` immediately after routing. This is because a critical case waiting unseen in `routed` is the worst case to miss, so `routed → escalated` is a valid transition.
- **Loop guard**: the rule only fires if the case has no earlier `CaseEvent` with `to_state == escalated`. Without it, a reviewer who de-escalates a critical case would see it re-escalate the next time it passed through routing. The guard checks event history rather than current state, so it also covers cases that were escalated, de-escalated, reclassified, and routed again.
- **Structure**: `should_auto_escalate_critical(case, was_escalated_before)` is a pure decision function. `case_was_escalated_before(db, case)` does the event log query. `auto_escalate_if_critical(db, case)` is the helper both routing calls use, so the two paths cannot drift apart.

### Manual escalation

- **Endpoint**: `POST /cases/{id}/escalate`, agent-guarded.
- **Input**: `reason`, required, whitespace-only values rejected with `422`.
- **Eligibility**: the state machine is the eligibility check. The case must be in `routed` or `in_review`. Any other state raises `InvalidTransitionError`, returned as `409 Conflict`, and the case is left unchanged. Escalating an already-escalated case is also a `409`.
- **Effect**: moves the case to `escalated` and writes a `CaseEvent` with `event_type="escalated"` and `notes="Manually escalated: <reason>"`.
- **Shared code path**: automatic and manual escalation both go through `escalate_case(db, case, reason)`.

### Deliberate scope cut: no time-based escalation

Non-critical cases are not escalated automatically. Reviewers instead see staleness directly through `time_in_state_hours`, which works like an age column in a Zendesk view which gives the reviewer the freedom to decide.

The intended update is a scheduled `escalate_overdue_cases` job, equivalent to a Zendesk automation, with per-severity time thresholds. It was deferred because it is the only part of the escalator that needs something running on a schedule, and `BackgroundTasks` cannot provide that. Measuring the clock from the latest event that moved a case into its current state means a de-escalated case gets a fresh clock, so the job would need no prior-escalation guard.

---

## Ordering and filtering

`GET /cases` is what makes escalation visible. Implemented in `app/services/case_queries.py`.

- **Filters**: optional `queue` and `state` query parameters, typed as the real enums, so invalid values are rejected with `422`. Resolved cases are not hidden by default.
- **Ordering**: escalated cases first, then by severity (`critical`, `high`, `medium`, `low`, unclassified last), then oldest, with `id` as a final tiebreak so ordering is deterministic.
- **Derived field `time_in_state_hours`**: float rounded to one decimal. Measured from the latest `CaseEvent` whose `to_state` equals the case's current state, falling back to `created_at` when no such event exists (for example a case still in `new`). Using the latest event means a de-escalated case gets a fresh clock. It is computed for the whole list in one grouped query, not one query per case. It is derived and not stored, so theres no extra column to keep consistent.

---

## Manual classification

For a case sitting in `manual_triage` meaning AI classification failed, an agent has two options: retrigger AI classification (`POST /cases/{id}/classify`, see AI integration above), or classify the case by hand.

- **Endpoint**: `POST /cases/{id}/classify-manually`, agent-guarded, same `require_agent` pattern as every other agent-facing endpoint
- **Input**: `severity`, `category`, both required, no `confidence` field since theres no AI confidence to report
- **Eligibility**: only valid when `case.state == in_review` and case.queue == Queue.manual_triage. Not available on a case that's already been classified, routed, escalated, or resolved, rejected with `409 Conflict`
- **Effect**: sets `Case.severity` and `Case.category` directly, transitions `in_review → classified`, no `pending_classification` detour, since no AI call is being made, that state's own definition wouldn't be true here
- **No `AiClassification` row is written.** That table is reserved for AI attempts specifically, per its own definition ("a record of one AI classification attempt"). A manual classification instead writes a `CaseEvent` with `event_type="manually_classified"`, which is also how source (AI vs human) is tracked, there is no separate `classification_source` column on `Case`, deliberately, to avoid tracking the same fact in two places when the event log already exists as the audit trail
- **Routing after manual classification**: identical to the AI path. Landing in `classified` triggers `route_case` automatically, then `auto_escalate_if_critical`. `CaseRouter` reads `category` off `Case` directly and doesn't treat a human-supplied value differently from an AI-supplied one. A manually classified critical case therefore ends in `escalated`, the same as an AI-classified one
- **Known limitation**: no field records which agent performed a manual classification. `require_agent` currently authenticates a role, not an identity, so there is no agent id to attach. This is the same gap named in "Known limitation: no role-based access control" below, manual classification inherits this rather than introducing a new one
---

## REST endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | /cases | list cases, agent-guarded. Optional `queue` and `state` filters. Ordered escalated first, then by severity, then oldest first. Each case includes `time_in_state_hours` |
| POST | /cases | create a case, returns the id only |
| GET | /cases/{id} | get a single case, agent-guarded |
| POST | /cases/{id}/classify | retrigger AI classification, agent-guarded |
| POST | /cases/{id}/classify-manually | manually classify a `manual_triage` case, agent-guarded |
| POST | /cases/{id}/escalate | manually escalate, agent-guarded, requires a `reason`. Valid from `routed` and `in_review`, `409` otherwise |
| POST | /cases/{id}/resolve | resolve a case (not yet built) |
| GET | /cases/{id}/events | get the event log for a case (not yet built) |

---

## Design decision: no case editing after submission

Cases are one-shot from the reporter's side. Like Depop for example, the reporter submits, review happens entirely on the platform side, with no visible edit or recall step.

Once a case is created, its description cannot be edited or overwritten. If a reporter needs to correct or add information, they submit a new case. This was chosen over an editable-until-classified approach because:
- It avoids a classification drifting from the text it was based on
- Multiple reports on one issue are preserved as separate signal, not collapsed into one edited record

## Design decision: queue is a function of category only

Queue is assigned from `category` alone, not `category` plus `severity`. Severity already has a job, driving escalation, so keeping queue as `f(category)` avoids two services both using severity for different effects. A reviewer in one queue sees every case of that type, with urgency expressed through escalation and ordering.

Consequences of this decision:
- Escalation changes state, never queue. A dedicated escalations queue was considered and rejected since it would break `queue = f(category)`, and because `escalated → in_review` exists, it would require storing each case's original queue so de-escalation could send it back.
- Urgency is made visible on the read side through ordering and `time_in_state_hours`, not through routing.

---

## Known limitation: no role-based access control

A lightweight role check exists via the X-Role header (app/auth.py), guarding `GET /cases`, `GET /cases/{id}`, and the agent-facing actions (`classify`, `classify-manually`, `escalate`) to require the agent role. This demonstrates the access-control decision logic, but it's not real authentication as anyone can set X-Role: agent themselves with no verification of identity. A production version would need real user accounts and verified identity.

One consequence is that no agent identity is recorded anywhere. Manual classification and manual escalation both write a `CaseEvent` that says what happened and why but not who did it, as `require_agent` authenticates a role and not a person.

`POST /cases` remains unguarded by role, since reporters are the intended caller, and returns a minimal `CaseSubmissionConfirmation` (id only) rather than the full case record for security purposes.

`state`, `severity`, `category`, and `queue` are deliberately withheld from the reporter because they leak internal process information. A reporter who can see their case's state move from `classified` back to `new` may learn that AI classification failed on their case. A reporter who can see `severity` or `category` outcomes over multiple reports could learn what phrasing produces a higher severity score. In a trust & safety context specifically, the reporter can sometimes be the person being investigated, so this is an information leak risk.

Without real auth, nothing stops a caller from setting the agent role themselves and seeing the full record. A production version would need role-based access control checking who is calling before deciding what to return, and would restrict agent endpoints to authenticated agents. This is a deliberate gap as this project is currently focused on the triage domain logic itself.

---

## Background jobs

Implemented via FastAPI's BackgroundTasks, triggered directly from create_case on POST /cases, not a separate job queue. This is a deliberate scope decision for a project this size. BackgroundTasks runs within the same server process and requires no extra infrastructure, at the cost of not surviving a server restart mid-task and not coordinating across multiple app instances.

- **ClassifyCaseJob** — implemented as `classify_case_background` in `app/main.py`, calls the AI classification service asynchronously so the create-case request doesn't block on it
- **AutoResolveJob** — auto-resolves cases meeting certain low-severity, no-activity criteria (not yet built)
- - **EscalationCheckJob**: deliberately not built, see "Escalation" above. `BackgroundTasks` is the wrong tool for it, because it only runs after a request and a stalled case produces no request

---

## Service layer

Rails spec used service objects, FastAPI equivalent is a `services/` module with plain Python classes or functions doing the same job.

- **AiClassifier** — wraps the Claude API call, handles the request/response and failure cases
- **CaseEscalator** (`case_escalator.py`): moves a case into `escalated`, serving both the automatic critical path and manual escalation. Changes state and writes a `CaseEvent`. It does not change the queue and has no notification side effects
- **CaseResolver** — handles the logic of closing a case (not yet built)
- **CaseQueries** (`case_queries.py`): the read side for `GET /cases`, covering filtering, ordering, and the derived `time_in_state_hours`
- **CaseTransitions** (`case_transitions.py`): `transition_case`, the single place that validates a transition, applies it, and writes the `CaseEvent`
- **CaseEscalator** — handles moving a case into escalated state and any side effects (notifications, queue reassignment)
