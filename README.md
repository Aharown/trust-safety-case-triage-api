# Trust & Safety Case Triage API

Case triage API with AI-assisted severity scoring, state machine routing, and queue escalation. Built with FastAPI and SQLAlchemy.

Modelled directly on real trust & safety operations. Cases come in, get classified by severity, routed to the right queue, and tracked through resolution states. AI provides a severity score and suggested category as input to the routing decision, it does not make the final call.

---

## Purpose

Built off hands-on trust & safety experience at Depop, investigating and mediating cases across fraud, prohibited items, and community guideline violations. The project translates these case judgements into a backend system, state machines to enforce valid case transitions and uses AI as an advisory signal not an authority.

---

## Stack

FastAPI, SQLAlchemy, Alembic, Postgres, Pydantic, pytest, Claude API (severity classification)

---

## Architecture

- **Data model**: cases with severity states and category classification
- **State machine**: hand-rolled transition validation, no illegal state changes
- **AI integration**: case description in, severity score and suggested category out, advisory only
- **Routing**: queue assignment and escalation logic driven by severity and category
- **Background processing**: async handling for queue routing and any long-running steps

---
