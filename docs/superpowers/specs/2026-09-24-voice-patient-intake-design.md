# Voice AI Patient Registration — Design

**Date:** 2026-09-24
**Context:** Take-home technical assessment, CareCloud, AI Engineer role. 3-hour budget.

## Goal

A caller dials a US phone number, speaks naturally with an AI intake coordinator, and a validated patient demographic record lands in a database that survives restarts. A REST API and a dashboard expose the stored records.

## Success criteria

The assessment is scored on five dimensions weighted equally. This design targets each one explicitly:

1. **Working system** — reviewers call the number, complete a registration, and find the record via the API. A second call does not lose the first.
2. **Conversational quality** — natural flow, handles corrections and out-of-order answers, reads back all fields before saving.
3. **Technical architecture** — telephony, LLM logic, data layer, and API are separable; schema is typed and constrained; endpoints are RESTful.
4. **Code quality & documentation** — README covers setup, architecture, stack justification, env vars, and trade-offs. The system prompt is committed.
5. **Edge cases & resilience** — invalid input, dropped calls, DB write failure, and "start over" all have defined behaviour.

Out of scope, stated by the brief itself: HIPAA compliance, real patient data, production uptime guarantees.

## Stack

| Layer | Choice | Justification |
|---|---|---|
| Telephony + voice | Vapi, free US inbound number | Issues a dialable US number with no payment method; free starter credits cover LLM, STT and TTS at cost. Handles turn-taking, barge-in and interruption — 20% of the score — that would take the whole budget to build on raw Twilio. |
| LLM | Billed through Vapi at cost | No separate provider key to manage or leak. |
| Backend | Python 3.12 + FastAPI | Pydantic expresses the 19-field validation declaratively and enforces the response envelope by type. Auto-generated `/docs` doubles as a reviewer-facing artifact. |
| Database | Neon (managed Postgres, free tier) | Survives redeploys. See "Why not SQLite" below. |
| Hosting | Render free web service, kept warm by a cron-job.org ping to `/health` every 10 minutes | Free, no card. The ping defeats the 15-minute idle sleep, which would otherwise cold-start the backend mid-call and time out the save. |
| Tests | pytest + httpx against in-memory SQLite | Fast, no external dependency. |

**Total infrastructure cost: $0.**

### Rejected: Twilio direct

Twilio trial accounts reject inbound calls whose caller ID is not verified in the account. Reviewers call from an unknown number, so a trial Twilio number cannot receive the evaluation call at all. Unblocking it requires upgrading the account and clearing a regulatory bundle for a US number from a non-US billing address — cost and latency the budget cannot absorb.

### Why not SQLite

Render's free tier provides no persistent disk. A SQLite file is lost on every redeploy and on container recycling, which fails the brief's explicit test: "If we register Jane Doe on Call 1, she must exist when we query on Call 2." Managed Postgres on Neon's free tier removes the risk for the same $0.

### Fallbacks

If Vapi does not issue a free number within the first 20 minutes, pivot to Retell AI ($10 free credits, $2/mo number). If Render's cold start proves disruptive despite the keep-alive, redeploy to Railway's 30-day trial credit, which does not sleep. Neither pivot changes application code — the voice webhook contract is the only vendor-facing surface.

## Architecture

```
Caller
  │ dials
  ▼
Vapi assistant  (STT · LLM · TTS · turn-taking · barge-in)
  │
  ├── tool call ──────────► POST /voice/tools ──┐
  │                                             │
  └── end-of-call report ─► POST /voice/report  │
                                                ├──► services/patients.py ──► Postgres
REST client ─────────────► /patients/*  ────────┘
Dashboard   ─────────────► GET /patients (same endpoint)
```

### Component boundaries

| Unit | Responsibility | Depends on |
|---|---|---|
| `models.py` | SQLAlchemy tables, constraints, indexes | database |
| `schemas.py` | Pydantic request/response models, field validators, spoken error text | nothing |
| `normalizers.py` | Spoken forms to structured values | nothing |
| `services/patients.py` | Create, get, list, update, soft-delete, find-by-phone. Pure Python, no HTTP types. | models |
| `api/patients.py` | REST adapter: HTTP status codes, `{data, error}` envelope | schemas, services |
| `api/voice.py` | Vapi adapter: tool dispatch, spoken error mapping, transcript capture | schemas, services, normalizers |
| `static/index.html` | Dashboard, reads `GET /patients` | the REST API only |

`normalizers.py` and `schemas.py` depend on nothing and are unit-testable in isolation. `services/patients.py` is testable without a web server. Each adapter can be changed without touching the other.

### Shared service layer, not self-HTTP

The brief permits the agent to "use the REST API **or** directly invoke the same service layer." This design uses the shared service layer. The voice webhook and the REST router are two thin adapters over one service module.

Rationale: the backend does not make HTTP calls to itself, and validation cannot drift between the phone path and the API path. The brief warns "do not rely solely on the voice agent for validation" — one service layer makes that structurally true rather than a convention. This is stated in the README.

## Validation

The central design decision: **validation lives in the backend and speaks back.**

Each validated field carries two error messages — a conventional API message, and a `spoken_error` phrased to be read aloud. When a tool call fails validation, the webhook returns:

```json
{ "result": "That date of birth is in the future. Please ask the caller for it again." }
```

Vapi feeds `result` back to the LLM as the tool's output, so the agent re-prompts for that field alone. No per-field re-prompting rules live in the system prompt, where they would drift.

### Field rules

| Field | Rule | Required |
|---|---|---|
| `first_name` | 1–50 chars, alphabetic plus hyphen and apostrophe | Yes |
| `last_name` | 1–50 chars, alphabetic plus hyphen and apostrophe | Yes |
| `date_of_birth` | Not in the future, within 130 years. The REST API accepts and returns ISO 8601 `YYYY-MM-DD`; the normalizer converts the brief's `MM/DD/YYYY` and spoken forms to ISO before validation. | Yes |
| `sex` | Enum: Male, Female, Other, Decline to Answer | Yes |
| `phone_number` | 10 digits after normalization; NANP area code and exchange may not begin with 0 or 1 | Yes |
| `email` | RFC-valid | No |
| `address_line_1` | 1–200 chars, non-blank | Yes |
| `address_line_2` | 0–200 chars | No |
| `city` | 1–100 chars | Yes |
| `state` | Member of the 50 states plus DC | Yes |
| `zip_code` | 5 digits, or ZIP+4 | Yes |
| `insurance_provider` | 0–100 chars | No |
| `insurance_member_id` | Alphanumeric, 0–50 chars | No |
| `preferred_language` | Default `English` | No |
| `emergency_contact_name` | 0–100 chars | No |
| `emergency_contact_phone` | Same rule as `phone_number` | No |
| `patient_id` | UUID, server-generated | Auto |
| `created_at` / `updated_at` | UTC timestamps, server-managed | Auto |

### Normalization

Speech transcripts are not clean input. `normalizers.py` runs before validation:

- State names to abbreviations (`"Texas"` to `TX`)
- Spelled digits to numerals (`"oh two one three eight"` to `02138`)
- Phone numbers stripped of country code, spaces, hyphens, parentheses
- Spoken dates to ISO (`"January fifth ninety two"` to `1992-01-05`)

Normalizing server-side rather than instructing the LLM to emit clean formats is what keeps the agent out of re-prompt loops on input that was actually correct.

## Data model

### `patients`

The 19 fields above, plus `deleted_at TIMESTAMP NULL` for soft delete. Indexes on `phone_number` (duplicate lookup, API filter) and `last_name` (API filter). Rows with `deleted_at IS NOT NULL` are excluded from list and get responses, which return 404.

### `call_transcripts`

`id`, `call_id`, `patient_id` (nullable FK), `transcript`, `summary`, `created_at`.

`patient_id` is nullable deliberately: a call that drops before confirmation produces a transcript with no patient, so the interaction is not lost.

## REST API

| Method | Endpoint | Behaviour |
|---|---|---|
| GET | `/patients` | List non-deleted. Optional `?last_name=`, `?date_of_birth=`, `?phone_number=` |
| GET | `/patients/{id}` | Single record, 404 if missing or soft-deleted |
| POST | `/patients` | Create, 201 with the record |
| PUT | `/patients/{id}` | Partial update, 200 |
| DELETE | `/patients/{id}` | Soft delete, sets `deleted_at`, 200 |

All responses use `{"data": ..., "error": null}` or `{"data": null, "error": {...}}`. Status codes: 200, 201, 400, 404, 422, 500.

## Voice agent

### Tools

| Tool | Purpose |
|---|---|
| `lookup_patient_by_phone` | Called early. Returns an existing record or null — this is the duplicate-detection behaviour the brief calls out. |
| `register_patient` | Called after read-back confirmation. Creates the record. |
| `update_patient` | Called when a returning caller chooses to update instead of create. |

Tool definitions live in `vapi/assistant.json`, committed to the repo. The system prompt lives in `prompts/system_prompt.md`, committed and commented. "Is the prompt engineering for the voice agent thoughtful and documented" is an explicit scoring line; leaving the prompt in a vendor dashboard forfeits it.

### Conversation design

The prompt establishes an intake-coordinator persona, collects required fields conversationally rather than as a fixed interrogation, accepts out-of-order and corrected answers, then offers optional fields as a single opt-in: "I can also take your insurance, emergency contact, and preferred language — would you like to add any of those?"

Before any write, the agent reads back every collected field and asks for confirmation. Only on confirmation does it call `register_patient`.

## Error handling

| Scenario | Behaviour |
|---|---|
| Invalid field value | 422 with `spoken_error`; the agent re-prompts for that field only |
| Database write fails | Tool returns a spoken apology; the full collected payload is logged to stdout so the data is recoverable |
| Call drops before confirmation | Nothing is written. Deliberate — a partial record is worse than none. The end-of-call webhook still stores the transcript. |
| Agent retries a save | Deduplicated on `(phone_number, date_of_birth)` within a 5-minute window; returns the existing record instead of creating a duplicate |
| Caller says "start over" | Handled in the prompt; the agent discards its working set and restarts collection. No tool call. |
| Returning caller detected | Agent offers to update rather than create, per the brief's bonus |

Every tool invocation logs its final collected payload to stdout, satisfying the observability requirement.

## Testing

pytest with httpx against in-memory SQLite, roughly ten tests covering:

- Each validation rule rejects bad input with 422
- Normalizers convert spoken forms correctly
- Soft-deleted records are absent from list and return 404 on get
- Duplicate lookup by phone finds an existing patient
- Response envelope shape holds on both success and error
- Partial update via PUT leaves untouched fields intact

## Repository layout

The application lives at the repository root; `docs/` holds this spec and the
implementation plan.

```
<repo root>/
  app/
    main.py
    config.py            # env vars only; no secrets in source
    db.py
    models.py
    schemas.py
    normalizers.py
    services/patients.py
    api/patients.py
    api/voice.py
    static/index.html    # dashboard
  prompts/system_prompt.md
  vapi/assistant.json
  tests/
  .env.example
  requirements.txt
  README.md
```

## Delivery sequence

Sequenced so that a callable, persisting system exists early and everything afterwards is additive. If time runs out, the cut happens at the end, not the middle.

```
0:00  Vapi account, free US number, verify inbound call on a default assistant
        GATE: no number by 0:20, pivot to Retell AI
0:20  Neon database provisioned; FastAPI skeleton DEPLOYED and reachable
        Deploying last is the common failure mode for this assessment
0:35  Models, schemas, normalizers, service layer, REST API
1:20  Voice tool webhook and Vapi tool definitions
1:45  System prompt, then live call iteration
2:15  Dashboard and transcript webhook
2:35  Test suite
2:50  README
```

## Deliberate omissions

Documented in the README under Next Steps rather than built:

- **Spanish / multi-language switching** — a bonus that risks the core flow
- **Appointment scheduling** — a bonus with no bearing on the scored dimensions
- **Authentication on the API** — the brief asks for a publicly testable endpoint
- **HIPAA controls, encryption at rest, audit logging** — explicitly out of scope

The brief states it would "rather see a simple system that works flawlessly than an over-engineered system that crashes on the first call." These omissions follow that instruction.

## Note on the deliverable list

The covering email requests a Dashboard; the assessment PDF lists a dashboard under bonus challenges. The email is the binding instruction, so the dashboard is treated as required scope, not a bonus.
