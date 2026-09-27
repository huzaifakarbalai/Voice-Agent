# Voice AI Patient Registration

A caller dials a phone number, speaks with a voice agent, and the agent
collects their demographics conversationally — name, date of birth, sex,
phone number, address, and optional insurance/emergency-contact/language
details. The agent validates as it goes, reads everything back for
confirmation, and saves the record to Postgres. The same records are
available through a REST API and a small dashboard. This is a technical
assessment project, not a production system: it stores no real patient data
and has no HIPAA controls.

**Fill these in once deployed:**

```
Phone number:    <PHONE_NUMBER>
API base URL:    <API_BASE_URL>
Dashboard:       <API_BASE_URL>/dashboard
API docs:        <API_BASE_URL>/docs
```

## Architecture

```
Caller
  │ dials
  ▼
Vapi assistant  (speech-to-text · LLM · text-to-speech · turn-taking · barge-in)
  │
  └── every server message (tool calls, end-of-call report, status
      updates) ──► POST /voice/webhook ──┐
                                         │
REST client ─────► /patients/*  ─────────┼──► app/services/patients.py ──► Postgres
Dashboard   ─────► GET /patients (same route) ──┘
```

`app/api/voice.py` (the Vapi adapter) and `app/api/patients.py` (the REST
adapter) are both thin layers over one shared module,
`app/services/patients.py`, which does all database access and knows
nothing about HTTP. Concretely:

- `app/models.py` — SQLAlchemy tables, constraints, indexes.
- `app/schemas.py` — Pydantic request/response models, field validators,
  and the spoken error text (see "Design decision" below).
- `app/normalizers.py` — turns spoken forms into structured values before
  validation runs.
- `app/services/patients.py` — create, get, list, update, soft-delete,
  find-by-phone, find-recent-duplicate. Plain Python in, ORM objects out;
  no FastAPI import anywhere in the file, so it is testable without a web
  server.
- `app/api/patients.py` — REST adapter: HTTP status codes, the `{data,
  error}` envelope.
- `app/api/voice.py` — Vapi adapter: tool-call dispatch, spoken-error
  mapping, transcript capture.
- `app/static/index.html` — the dashboard; reads `GET /patients` like any
  other REST client.

**Why a shared service layer instead of the backend calling its own REST
API over HTTP:** the phone path and the API path can never validate a
field differently or apply soft-delete differently, because they call the
exact same functions. There is also no self-HTTP-call latency or failure
mode to reason about (DNS, TLS, the backend being temporarily unable to
reach itself). The alternative — the voice webhook issuing HTTP requests
to `/patients` — was considered and rejected for exactly this reason: two
code paths that are supposed to agree, kept in sync only by convention.

## Tech stack and why

| Layer | Choice | Why |
|---|---|---|
| Telephony + voice | Vapi | Free US inbound number, no card required. Handles speech-to-text, the LLM turn, text-to-speech, turn-taking, and barge-in — building that on raw telephony would be a project on its own. |
| Backend | Python 3.12 + FastAPI | Async, small, and gives free auto-generated OpenAPI docs at `/docs`, which double as a reviewer-facing artifact. |
| Validation | Pydantic (`app/schemas.py`) | Declarative validation for the patient schema — 16 input fields on `PatientBase`/`PatientCreate` (19 total attributes once the three server-generated ones, `patient_id`, `created_at`, `updated_at`, are counted on `PatientOut`) — with `field_validator`s that raise `ValueError` with a message the rest of the code can key off, rather than validation logic scattered across route handlers. |
| Database | Postgres (Neon, free tier) | Render's free web service has no persistent disk, so anything on local SQLite is lost on every redeploy or container recycle. Postgres on Neon survives that; the app must still work the day after a redeploy. |
| ORM | SQLAlchemy 2.0 (`Mapped`/`mapped_column` style) | One model definition (`app/models.py`) runs unmodified against Postgres in production and SQLite in tests. |
| Hosting | Render free web service | Free, deploys straight from GitHub. Sleeps after 15 minutes idle — mitigated with an external keep-alive ping to `/health` (see Trade-offs). |
| Tests | pytest, in-memory SQLite | Fast, hermetic, no network dependency; 102 tests. |

## Design decision: validation speaks

This is the central design decision in the codebase. Validation rules and
the *caller-readable sentence* for each failure both live in
`app/schemas.py`:

```python
SPOKEN_ERRORS: dict[str, str] = {
    "phone_number": "That phone number is not a valid ten digit US number. "
                    "Please ask the caller to repeat their phone number "
                    "including the area code.",
    ...
}
```

`spoken_error_for(exc)` turns a Pydantic `ValidationError` into one of
these sentences. When `app/api/voice.py` catches a validation failure from
`register_patient` or `update_patient`, it hands that sentence straight
back to Vapi as the tool call's result string — not a JSON error object,
because Vapi feeds the string back to the model as the tool's output
verbatim.

The payoff: the system prompt (`prompts/system_prompt.md`) contains no
per-field re-prompting rules at all. It says, generically, "if a tool
tells you a value was invalid, ask the caller again for only that one
field" — and the *specific* field and *specific* phrasing come from the
tool result every time. A prompt instruction like "re-prompt for invalid
fields" is exactly the kind of rule a model drifts from over a long
conversation; a tool result that states precisely what went wrong does
not, because the model isn't asked to remember a rule, only to read a
sentence it was just handed.

`app/normalizers.py` runs before this validation layer — converting
"Texas" to `TX`, "oh two one three eight" to `02138`, spoken dates like
"January fifth nineteen ninety-two" to an ISO date, and spelled-out phone
numbers to digits — precisely so the agent is never made to re-prompt for
a value the caller already got right; only genuinely invalid input reaches
the validators and their spoken errors.

## Setup

```bash
git clone <repo-url>
cd <repo>
pip install -r requirements.txt
cp .env.example .env        # then edit .env — see Environment variables below
uvicorn app.main:app --reload
```

To run the test suite, install the dev requirements as well (`pytest` and
`httpx` are not part of the runtime deployment, so they live in a separate
file rather than in `requirements.txt`):

```bash
pip install -r requirements-dev.txt
pytest                        # 102 tests, in-memory SQLite, no external services needed
```

The app defaults `DATABASE_URL` to a local SQLite file
(`sqlite+pysqlite:///./local.db`) if unset, so `uvicorn app.main:app
--reload` works with zero configuration for local exploration. Point
`DATABASE_URL` at Postgres for anything resembling production use.

## Environment variables

Real values are never committed. `.env.example` at the repo root lists the
variable names with placeholder values — copy it to `.env` and fill in
your own.

| Variable | Used by | Purpose |
|---|---|---|
| `DATABASE_URL` | the app (`app/config.py`) | Postgres connection string. `postgresql://` and `postgres://` are rewritten to `postgresql+psycopg://` automatically, since SQLAlchemy 2.0 needs the driver named and this project installs `psycopg` 3. Falls back to a local SQLite file if unset. |
| `VAPI_SECRET` | the app (`app/config.py`, consumed in `app/api/voice.py`) | Shared secret Vapi sends as the `x-vapi-secret` header on every webhook request. See "Security posture" below. |
| `VAPI_API_KEY` | `scripts/push_assistant.py` only | Vapi private API key, used to PATCH the assistant config. Never read by the running app. |
| `VAPI_ASSISTANT_ID` | `scripts/push_assistant.py` only | The Vapi assistant to update. |
| `BACKEND_URL` | `scripts/push_assistant.py` only | Deployed backend base URL; substituted into the tool `server.url` fields and the assistant-level `server.url` in `vapi/assistant.json` before pushing. |

`render.yaml` additionally sets `PYTHON_VERSION` (`3.12.7`) as a Render
build-time env var. That is a Render platform setting, not something the
application reads at runtime (`app/config.py` never looks it up), so it has
no corresponding entry in `.env.example` and does not need to be set locally.

## API reference

Every response — success or failure — is one envelope:

```json
{"data": ..., "error": null}
{"data": null, "error": {"code": "...", "message": "...", "details": null}}
```

All five endpoints are under `/patients`. Deletes are **soft** — they set
`deleted_at` and the row disappears from list/get, but the row itself is
never removed.

Set this once, substituting the real deployed URL for `<your-deployed-url>`
(the same value that fills `<API_BASE_URL>` at the top of this file), and
every example below is copy-pasteable as-is:

```bash
export API_BASE_URL=<your-deployed-url>
```

**List, with optional filters**

```bash
curl "$API_BASE_URL/patients"
curl "$API_BASE_URL/patients?last_name=Doe"
curl "$API_BASE_URL/patients?date_of_birth=1992-01-05"
curl "$API_BASE_URL/patients?phone_number=4155551234"
```

**Get one by id**

```bash
curl "$API_BASE_URL/patients/<patient_id>"
```

**Create**

```bash
curl -X POST "$API_BASE_URL/patients" \
  -H "Content-Type: application/json" \
  -d '{
        "first_name": "Jane", "last_name": "Doe",
        "date_of_birth": "1992-01-05", "sex": "Female",
        "phone_number": "4155551234",
        "address_line_1": "1 Market Street", "city": "San Francisco",
        "state": "CA", "zip_code": "94105"
      }'
```

**Update (partial — send only changed fields)**

```bash
curl -X PUT "$API_BASE_URL/patients/<patient_id>" \
  -H "Content-Type: application/json" \
  -d '{"phone_number": "4155550199"}'
```

**Delete (soft)**

```bash
curl -X DELETE "$API_BASE_URL/patients/<patient_id>"
```

Other routes: `GET /health` (also the keep-alive ping target),
`GET /dashboard` (the record-browsing UI), `GET /docs` (FastAPI's
auto-generated OpenAPI explorer), and `POST /voice/webhook`, which is not
meant to be called by hand — see below.

## Prompt engineering

The voice agent's behaviour is defined by two committed files:

- `prompts/system_prompt.md` — the system prompt: how to speak, what to
  collect and in what order, when to call each tool, and the confirmation
  script.
- `vapi/assistant.json` — the assistant config: voice/transcriber choice,
  the three tool definitions (name, JSON-schema parameters, server URL),
  and call limits.

`scripts/push_assistant.py` is the one-way sync from repo to Vapi: it
reads `vapi/assistant.json`, inlines the current contents of
`prompts/system_prompt.md` into the placeholder system message, substitutes
`BACKEND_URL` into every `server.url`, and PATCHes the Vapi assistant.
Keeping both files in git rather than editing them in the Vapi dashboard
means the prompt is reviewable and diffable like any other code, and the
repo — not whatever a person last typed into the dashboard — is the
authoritative source. Running the script again always overwrites the
dashboard's copy.

There are three function tools, all handled by the single webhook below, plus
Vapi's built-in `endCall` tool so the agent can hang up cleanly once the caller
is confirmed:
`lookup_patient_by_phone` (checks for an existing record by phone number so
a returning caller is recognised and offered an update instead of a
duplicate registration), `register_patient`, and `update_patient`.

### One webhook endpoint, dispatched by message type

Vapi posts every server message for an assistant — tool calls, the
end-of-call report, status updates, transcripts — to a single configured
URL. `POST /voice/webhook` (`app/api/voice.py`) reflects that: it reads
`message.type` from the request body and dispatches to a tool-call handler
or the end-of-call handler accordingly, rather than exposing one route per
message type. See "Trade-offs" for why this differs from the original
design, which specified two separate endpoints.

### Security posture

The webhook fails closed: `verify_secret()` rejects **every** request with
401 if `VAPI_SECRET` is unset, rather than accepting unauthenticated
requests when the operator forgot to configure it. The comparison uses
`secrets.compare_digest` to avoid a timing side-channel on the secret.
`app/main.py` logs a startup warning when `VAPI_SECRET` is missing, so a
misconfigured deploy shows up as one clear log line instead of a wall of
mysterious 401s.

## Trade-offs and known limitations

- **One `/voice/webhook` endpoint instead of two.** The original design
  described separate tool-call and end-of-call-report endpoints. Vapi
  actually posts all server messages for an assistant to one configured
  URL, discriminated by `message.type` — so two endpoints would mean
  configuring the same URL twice and branching inside FastAPI's routing
  layer instead of inside the handler. One endpoint that dispatches on
  `message.type` matches how the platform actually calls it.
- **`Base.metadata.create_all()` instead of Alembic migrations.** Fine for
  a greenfield schema created once at startup (`app/main.py`); it is not a
  safe way to evolve a schema that already holds data, since it can only
  add new tables, not alter or version existing ones. A real deployment
  with a live schema would need Alembic.
- **No authentication on the REST API.** The assessment asks for a
  publicly testable endpoint, so `/patients/*` has no auth. This would be
  unacceptable in a real system handling patient data — it stores full
  demographic records with zero access control end to end.
- **Free-tier hosting sleeps after 15 minutes idle.** A call landing on a
  cold instance would time out mid-save while the agent waits. Mitigated
  by an external keep-alive ping (`render.yaml`'s `healthCheckPath`, plus a
  cron-job.org job hitting `<API_BASE_URL>/health` every 10 minutes, so the
  instance never sits idle long enough to suspend) rather than paying for
  an always-on instance.
- **Vapi's free phone numbers are US-domestic inbound only.** No
  international callers, no outbound calling.
- **Transcript-to-patient correlation is in-process and unpersisted.**
  `app/api/voice.py` keeps a bounded `call_id -> patient_id` mapping in a
  module-level `OrderedDict` (capped at 256 entries, oldest evicted first)
  to link an end-of-call report to the patient that call actually touched
  — this matters on a shared household line, where phone-number lookup
  alone could attach the wrong child's call to a sibling's most recent
  record. On a multi-worker deployment or a process restart mid-call, that
  mapping is gone and the code falls back to phone-number lookup, which is
  usually right but not guaranteed to be for shared lines. A single Render
  free-tier instance does not run multiple workers, so this is a real but
  narrow gap.
- **Timestamp columns reject naive datetimes.** `UTCDateTime` in
  `app/models.py` is a `TypeDecorator` that raises on write if a naive
  `datetime` is passed, and normalizes every value read back to
  timezone-aware UTC. This is stricter than most codebases, which usually
  either trust every caller to pass UTC or don't check at all — the reason
  is that Postgres returns aware datetimes from a `timestamptz` column but
  SQLite (used for every test) silently returns naive ones from the same
  `DateTime(timezone=True)` mapping. Without the check, code that compares
  `created_at` against `datetime.now(timezone.utc)` — as
  `find_recent_duplicate` does — would raise `TypeError` on SQLite while
  working on Postgres, or worse, silently rely on both sides formatting to
  the same string. The strict write-side check surfaces a naive datetime
  immediately at the call site that produced it, rather than as a
  dialect-dependent bug months later.
- **No real patient data.** This is a demonstration system built for a
  technical assessment. It has no HIPAA controls (encryption at rest,
  audit logging, BAAs, access controls) and must not be used with real
  patient information.

## Next steps

Deliberately deferred to keep the core call-to-database flow reliable
first, per the assessment's own stated preference:

- Spanish (and other multi-language) support in the voice agent.
- Appointment scheduling, beyond intake.
- Authentication on the REST API.
- Alembic migrations, ahead of any schema change to a system with real data.
- A browser-driven (e.g. Playwright) test for the dashboard, to complement
  the 102 backend tests, which do not currently exercise `static/index.html`.
