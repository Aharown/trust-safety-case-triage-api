# Trust & Safety Case Triage API

Case triage API with AI-assisted severity scoring, state machine routing, and queue escalation. Built with FastAPI and SQLAlchemy.

Modelled directly on real trust & safety operations. Cases come in, get classified by severity, routed to the right queue, and tracked through resolution states. AI provides a severity score and suggested category as input to the routing decision, it does not make the final call.

**Live demo:** https://trust-safety-case-triage-api.onrender.com/

## Purpose

Built off hands-on trust & safety experience at Depop, investigating and mediating cases across fraud, prohibited items, and community guideline violations. The project translates these case judgements into a backend system, state machines to enforce valid case transitions and uses AI as an advisory signal not an authority.

## Stack

FastAPI, SQLAlchemy, Alembic, Postgres, Pydantic, pytest, Claude API (severity classification)

## Architecture

- **Data model:** cases with severity, category and state, plus an event log that records every state change as an audit trail, and a record of each AI classification attempt
- **State machine:** hand-rolled transition validation, no illegal state changes. Every change goes through one function that checks the move and writes the audit event together
- **AI integration:** case description in, severity score and suggested category out, advisory only. Uses forced tool-use so the API enforces the response shape instead of the code parsing free text
- **Routing:** the queue is chosen from the category alone. Severity drives escalation and ordering, so the two never compete. Critical cases are escalated automatically
- **Background processing:** classification and routing run after the response is sent, using FastAPI `BackgroundTasks`

For the state machine, endpoints, design decisions and known limitations, see [docs/spec.md](docs/spec.md).

## Running it locally

You need Python and a local PostgreSQL.

```bash
git clone <repo-url>
cd trust-safety-case-triage-api
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

createdb case_triage
echo 'ANTHROPIC_API_KEY=your-key-here' > .env

alembic upgrade head
uvicorn app.main:app --reload
```

By default the app connects to `postgresql://localhost/case_triage`. To use a different database, set the `DATABASE_URL` environment variable.

Interactive API docs are served at `http://localhost:8000/docs`.

## Try it

Agent endpoints need the header `X-Role: agent`. This is a lightweight role check, not real authentication.

```bash
curl -X POST http://localhost:8000/cases \
  -H "Content-Type: application/json" \
  -d '{"description": "Seller took my money and never posted the item", "reported_entity_type": "user", "reported_entity_id": 42}'

curl http://localhost:8000/cases -H "X-Role: agent"

curl -X POST http://localhost:8000/cases/1/pickup -H "X-Role: agent"

curl -X POST http://localhost:8000/cases/1/resolve \
  -H "X-Role: agent" -H "Content-Type: application/json" \
  -d '{"resolution": "Seller suspended"}'

curl http://localhost:8000/cases/1/events -H "X-Role: agent"
```

## Tests

```bash
python -m pytest
```

The normal run uses a fake classifier, so it is fast, free and needs no API key.

Tests that call the real Claude API are marked `integration`, are skipped by default, and cost a small amount of credit:

```bash
python -m pytest -m integration -v -s
```

The test suite rebuilds the tables of the configured database, so it refuses to run against anything that is not a local database.

## Deployment

https://trust-safety-case-triage-api.onrender.com. Interactive API docs, where you can send requests from the browser. Agent endpoints need an `x-role` field set to `agent`. `POST /cases` needs no role as it assumes the reporter role by default.

The app runs on Render's free plan, with the database on Neon's free plan. Both free tiers sleep when idle, so the first request after a quiet spell can take a minute or two. Each new case triggers a real Claude call on a spend-limited key, so please keep test submissions small.

Configuration is through two environment variables, `DATABASE_URL` and `ANTHROPIC_API_KEY`. Migrations run on every start with `alembic upgrade head`. See the Deployment section of [docs/spec.md](docs/spec.md) for the full setup.
