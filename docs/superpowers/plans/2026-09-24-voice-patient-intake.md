# Voice AI Patient Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A caller dials a US phone number, registers as a patient through natural conversation, and the validated record persists in Postgres and is retrievable through a REST API and a dashboard.

**Architecture:** Vapi hosts the voice assistant (STT, LLM, TTS, turn-taking) and calls our backend over HTTPS tool webhooks. A FastAPI backend exposes two thin adapters — a REST router and a Vapi webhook router — over one shared `services/patients.py`. Validation lives in Pydantic schemas and returns caller-readable error text so the agent can re-prompt for a single field.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Pydantic v2, Postgres (Neon) in production, SQLite in tests, pytest + httpx.

**Spec:** `docs/superpowers/specs/2026-09-24-voice-patient-intake-design.md`

## Global Constraints

- Python 3.12. FastAPI. SQLAlchemy 2.0 style (`mapped_column`, `Session`).
- Every API response uses the envelope `{"data": ..., "error": null}` or `{"data": null, "error": {...}}`. No bare bodies, no bare lists.
- Status codes in use: 200, 201, 400, 404, 422, 500.
- `patient_id` is a UUID4 stored as `String(36)` so the same models run on Postgres and on SQLite in tests.
- Dates cross the REST boundary as ISO 8601 `YYYY-MM-DD`. `MM/DD/YYYY` and spoken forms are converted by `normalizers.py` before validation.
- DELETE is always a soft delete — set `deleted_at`, never remove the row. Soft-deleted rows are invisible to list and get.
- No secrets in source. Every credential is read from an environment variable in `app/config.py`.
- Every voice tool invocation logs its resolved payload to stdout.
- The application lives at the repository root. `docs/` holds this plan and the spec.

## Deviation from the spec

The spec describes two voice endpoints, `POST /voice/tools` and `POST /voice/report`. Vapi delivers all server messages for an assistant to a single configured server URL, discriminated by `message.type`. This plan therefore implements **one** endpoint, `POST /voice/webhook`, which dispatches on `message.type` to a tool handler and a report handler. The internal split the spec asked for is preserved as two functions. Record this in the README.

## File Structure

| File | Responsibility |
|---|---|
| `app/config.py` | Read env vars. Normalize the Neon URL to the `psycopg` driver. |
| `app/db.py` | Engine, session factory, declarative `Base`, `get_db` dependency. |
| `app/envelope.py` | Envelope helpers and the exception handlers that enforce it. |
| `app/normalizers.py` | Spoken and loose input to structured values. Pure functions, no imports from the app. |
| `app/schemas.py` | Pydantic models, field validators, spoken error text. No imports from the app. |
| `app/models.py` | `Patient` and `CallTranscript` tables. |
| `app/services/patients.py` | All database logic. Pure Python in, ORM objects out. No HTTP types. |
| `app/api/patients.py` | REST adapter. |
| `app/api/voice.py` | Vapi adapter: tool dispatch, spoken errors, transcript capture. |
| `app/static/index.html` | Dashboard. Reads `GET /patients` only. |
| `app/main.py` | App assembly, router mounting, `/health`. |
| `prompts/system_prompt.md` | The agent's system prompt, committed and commented. |
| `vapi/assistant.json` | Assistant config and tool definitions, committed. |
| `scripts/push_assistant.py` | Pushes `vapi/assistant.json` to Vapi via API. |

---

### Task 1: Project scaffold, config, and health check

**Files:**
- Create: `requirements.txt`, `.env.example`, `.gitignore`, `app/__init__.py`, `app/config.py`, `app/db.py`, `app/envelope.py`, `app/main.py`
- Test: `tests/conftest.py`, `tests/test_health.py`

**Interfaces:**
- Consumes: nothing
- Produces: `app.config.settings` (attributes `database_url: str`, `vapi_secret: str | None`); `app.db.Base`, `app.db.get_db`, `app.db.engine`, `app.db.SessionLocal`; `app.envelope.ok(data) -> dict`, `app.envelope.error_body(code: str, message: str, details=None) -> dict`, `app.envelope.install_exception_handlers(app)`; `app.main.app`

- [ ] **Step 1: Create the dependency and ignore files**

`requirements.txt`:

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
sqlalchemy==2.0.36
psycopg[binary]==3.2.3
pydantic[email]==2.10.4
python-dateutil==2.9.0.post0
python-dotenv==1.0.1
pytest==8.3.4
httpx==0.28.1
```

`.gitignore`:

```
__pycache__/
*.pyc
.env
.venv/
venv/
.pytest_cache/
*.db
```

`.env.example`:

```
# Postgres connection string from Neon. Never commit the real value.
DATABASE_URL=postgresql://user:password@host.neon.tech/dbname?sslmode=require
# Shared secret configured on the Vapi assistant's server URL. Sent as the x-vapi-secret header.
VAPI_SECRET=change-me
```

- [ ] **Step 2: Write the failing health test**

`tests/conftest.py`:

```python
import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("VAPI_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app

# A single in-memory SQLite connection shared by every session in a test,
# so tables created in the fixture are visible to the request handlers.
test_engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def db():
    Base.metadata.create_all(bind=test_engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
```

`tests/test_health.py`:

```python
def test_health_returns_envelope(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"data": {"status": "ok"}, "error": None}
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest tests/test_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 4: Write config, db, envelope, and main**

`app/__init__.py`: empty file.

`app/config.py`:

```python
import os

from dotenv import load_dotenv

load_dotenv()


def _normalize_database_url(raw: str) -> str:
    """Neon hands out postgresql:// URLs. SQLAlchemy 2.0 needs the driver named
    explicitly, and this project installs psycopg 3 rather than psycopg2."""
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+psycopg://", 1)
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+psycopg://", 1)
    return raw


class Settings:
    def __init__(self) -> None:
        self.database_url = _normalize_database_url(
            os.environ.get("DATABASE_URL", "sqlite+pysqlite:///./local.db")
        )
        # Optional. When set, /voice/webhook rejects requests without a matching header.
        self.vapi_secret = os.environ.get("VAPI_SECRET") or None


settings = Settings()
```

`app/db.py`:

```python
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
```

`app/envelope.py`:

```python
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


def ok(data):
    return {"data": data, "error": None}


def error_body(code: str, message: str, details=None):
    return {"data": None, "error": {"code": code, "message": message, "details": details}}


def install_exception_handlers(app: FastAPI) -> None:
    """Every error path returns the same envelope the success paths use."""

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_body("validation_error", "One or more fields are invalid.", details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def on_http_error(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body("http_error", str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def on_unhandled_error(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", "An unexpected error occurred."),
        )
```

`app/main.py`:

```python
import logging

from fastapi import FastAPI

from app.envelope import install_exception_handlers, ok

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="Voice AI Patient Registration", version="1.0.0")
install_exception_handlers(app)


@app.get("/health")
def health():
    """Also the target of the external keep-alive ping that stops the free
    hosting tier from sleeping between calls."""
    return ok({"status": "ok"})
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .gitignore .env.example app tests
git commit -m "feat: scaffold FastAPI app with response envelope and health check"
```

---

### Task 2: Input normalizers

**Files:**
- Create: `app/normalizers.py`
- Test: `tests/test_normalizers.py`

**Interfaces:**
- Consumes: nothing
- Produces: `normalize_phone(raw) -> str | None`, `normalize_state(raw) -> str | None`, `normalize_zip(raw) -> str | None`, `normalize_date(raw) -> datetime.date | None`, `normalize_sex(raw) -> str | None`, `normalize_patient_payload(payload: dict) -> dict`

- [ ] **Step 1: Write the failing tests**

`tests/test_normalizers.py`:

```python
from datetime import date

from app.normalizers import (
    normalize_date,
    normalize_patient_payload,
    normalize_phone,
    normalize_sex,
    normalize_state,
    normalize_zip,
)


def test_phone_strips_formatting_and_country_code():
    assert normalize_phone("+1 (415) 555-0142") == "4155550142"
    assert normalize_phone("415.555.0142") == "4155550142"


def test_phone_accepts_spelled_digits():
    assert normalize_phone("four one five five five five oh one four two") == "4155550142"


def test_phone_rejects_wrong_length():
    assert normalize_phone("555") is None
    assert normalize_phone("") is None
    assert normalize_phone(None) is None


def test_state_accepts_full_name_and_abbreviation():
    assert normalize_state("Texas") == "TX"
    assert normalize_state("  new york ") == "NY"
    assert normalize_state("ca") == "CA"
    assert normalize_state("Freedonia") is None


def test_zip_accepts_five_and_plus_four_and_spelled_digits():
    assert normalize_zip("94110") == "94110"
    assert normalize_zip("94110-1234") == "94110-1234"
    assert normalize_zip("oh two one three eight") == "02138"
    assert normalize_zip("941") is None


def test_date_accepts_iso_us_and_spoken_forms():
    assert normalize_date("1992-01-05") == date(1992, 1, 5)
    assert normalize_date("01/05/1992") == date(1992, 1, 5)
    assert normalize_date("January 5th, 1992") == date(1992, 1, 5)
    assert normalize_date("not a date") is None


def test_sex_maps_loose_input_to_enum():
    assert normalize_sex("male") == "Male"
    assert normalize_sex("F") == "Female"
    assert normalize_sex("prefer not to say") == "Decline to Answer"
    assert normalize_sex("banana") is None


def test_payload_normalizes_known_fields_and_leaves_others():
    payload = {
        "first_name": "  Jane ",
        "phone_number": "+1 415 555 0142",
        "state": "California",
        "date_of_birth": "01/05/1992",
        "sex": "female",
        "insurance_provider": "Aetna",
    }
    result = normalize_patient_payload(payload)
    assert result["first_name"] == "Jane"
    assert result["phone_number"] == "4155550142"
    assert result["state"] == "CA"
    assert result["date_of_birth"] == "1992-01-05"
    assert result["sex"] == "Female"
    assert result["insurance_provider"] == "Aetna"


def test_payload_passes_unnormalizable_values_through_for_validation_to_reject():
    result = normalize_patient_payload({"phone_number": "555", "state": "Freedonia"})
    assert result["phone_number"] == "555"
    assert result["state"] == "Freedonia"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_normalizers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.normalizers'`

- [ ] **Step 3: Write the normalizers**

`app/normalizers.py`:

```python
"""Speech transcripts are not clean input. These functions run before validation
so the agent is never asked to re-prompt for a value the caller actually got right.

Every function returns None when it cannot confidently normalize. The caller
passes the original value through so the validation layer produces the error
message, keeping all rejection text in one place.
"""

import re
from datetime import date, datetime

from dateutil import parser as date_parser

STATE_ABBREVIATIONS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}

STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "washington d.c.": "DC",
}

DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
    "nine": "9",
}

ORDINAL_WORDS = {
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
    "sixth": "6", "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10",
    "eleventh": "11", "twelfth": "12", "thirteenth": "13", "fourteenth": "14",
    "fifteenth": "15", "sixteenth": "16", "seventeenth": "17",
    "eighteenth": "18", "nineteenth": "19", "twentieth": "20",
    "twenty-first": "21", "twenty-second": "22", "twenty-third": "23",
    "twenty-fourth": "24", "twenty-fifth": "25", "twenty-sixth": "26",
    "twenty-seventh": "27", "twenty-eighth": "28", "twenty-ninth": "29",
    "thirtieth": "30", "thirty-first": "31",
}

SEX_SYNONYMS = {
    "m": "Male", "male": "Male", "man": "Male",
    "f": "Female", "female": "Female", "woman": "Female",
    "other": "Other", "non-binary": "Other", "nonbinary": "Other",
    "decline to answer": "Decline to Answer", "decline": "Decline to Answer",
    "prefer not to say": "Decline to Answer", "prefer not to answer": "Decline to Answer",
    "no answer": "Decline to Answer", "skip": "Decline to Answer",
}

ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")


def _words_to_digits(raw: str) -> str:
    """Replace spelled-out digits with numerals, leaving everything else alone."""
    tokens = re.split(r"[\s\-,]+", raw.strip().lower())
    return "".join(DIGIT_WORDS.get(token, token) for token in tokens)


def normalize_phone(raw) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", _words_to_digits(str(raw)))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def normalize_state(raw) -> str | None:
    if not raw:
        return None
    text = str(raw).strip()
    if text.upper() in STATE_ABBREVIATIONS:
        return text.upper()
    return STATE_NAMES.get(re.sub(r"\s+", " ", text.lower()))


def normalize_zip(raw) -> str | None:
    if not raw:
        return None
    text = str(raw).strip()
    if ZIP_RE.match(text):
        return text
    digits = re.sub(r"\D", "", _words_to_digits(text))
    if len(digits) == 5:
        return digits
    if len(digits) == 9:
        return f"{digits[:5]}-{digits[5:]}"
    return None


def normalize_date(raw) -> date | None:
    if not raw:
        return None
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    # Spoken forms: "January fifth 1992", "January 5th, 1992".
    lowered = text.lower()
    for word, numeral in ORDINAL_WORDS.items():
        lowered = re.sub(rf"\b{re.escape(word)}\b", numeral, lowered)
    lowered = re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1", lowered)
    try:
        return date_parser.parse(lowered, dayfirst=False).date()
    except (ValueError, OverflowError, TypeError):
        return None


def normalize_sex(raw) -> str | None:
    if not raw:
        return None
    return SEX_SYNONYMS.get(re.sub(r"\s+", " ", str(raw).strip().lower()))


def normalize_patient_payload(payload: dict) -> dict:
    """Normalize the fields we know how to normalize. Anything that fails to
    normalize is passed through unchanged so the schema layer rejects it with
    the right message."""
    result = dict(payload)

    for key in (
        "first_name", "last_name", "address_line_1", "address_line_2", "city",
        "insurance_provider", "insurance_member_id", "preferred_language",
        "emergency_contact_name", "email",
    ):
        if isinstance(result.get(key), str):
            result[key] = result[key].strip()

    for key in ("phone_number", "emergency_contact_phone"):
        if result.get(key) is not None:
            result[key] = normalize_phone(result[key]) or result[key]

    if result.get("state") is not None:
        result["state"] = normalize_state(result["state"]) or result["state"]

    if result.get("zip_code") is not None:
        result["zip_code"] = normalize_zip(result["zip_code"]) or result["zip_code"]

    if result.get("sex") is not None:
        result["sex"] = normalize_sex(result["sex"]) or result["sex"]

    if result.get("date_of_birth") is not None:
        parsed = normalize_date(result["date_of_birth"])
        result["date_of_birth"] = parsed.isoformat() if parsed else result["date_of_birth"]

    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_normalizers.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add app/normalizers.py tests/test_normalizers.py
git commit -m "feat: normalize spoken and loose input before validation"
```

---

### Task 3: Database models

**Files:**
- Create: `app/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `app.db.Base`
- Produces: `Patient` (columns `patient_id`, `first_name`, `last_name`, `date_of_birth`, `sex`, `phone_number`, `email`, `address_line_1`, `address_line_2`, `city`, `state`, `zip_code`, `insurance_provider`, `insurance_member_id`, `preferred_language`, `emergency_contact_name`, `emergency_contact_phone`, `created_at`, `updated_at`, `deleted_at`); `CallTranscript` (columns `id`, `call_id`, `patient_id`, `transcript`, `summary`, `created_at`)

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:

```python
from datetime import date

from app.models import CallTranscript, Patient


def test_patient_gets_uuid_and_timestamps_on_insert(db):
    patient = Patient(
        first_name="Jane",
        last_name="Doe",
        date_of_birth=date(1992, 1, 5),
        sex="Female",
        phone_number="4155550142",
        address_line_1="1 Market St",
        city="San Francisco",
        state="CA",
        zip_code="94105",
    )
    db.add(patient)
    db.commit()

    assert len(patient.patient_id) == 36
    assert patient.created_at is not None
    assert patient.updated_at is not None
    assert patient.deleted_at is None
    assert patient.preferred_language == "English"


def test_transcript_allows_null_patient_so_dropped_calls_are_still_recorded(db):
    transcript = CallTranscript(call_id="call-1", transcript="hello", summary="greeting")
    db.add(transcript)
    db.commit()

    assert transcript.patient_id is None
    assert transcript.created_at is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 3: Write the models**

`app/models.py`:

```python
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid4_str() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Patient(Base):
    __tablename__ = "patients"

    # String(36) rather than a native UUID type so the same model runs on
    # Postgres in production and on SQLite in the test suite.
    patient_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid4_str)

    first_name: Mapped[str] = mapped_column(String(50), nullable=False)
    last_name: Mapped[str] = mapped_column(String(50), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    sex: Mapped[str] = mapped_column(String(20), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(10), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)

    address_line_1: Mapped[str] = mapped_column(String(200), nullable=False)
    address_line_2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    zip_code: Mapped[str] = mapped_column(String(10), nullable=False)

    insurance_provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    insurance_member_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    preferred_language: Mapped[str] = mapped_column(String(50), nullable=False, default="English")
    emergency_contact_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(10), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    # Soft delete. Rows with a value here are invisible to list and get.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_patients_phone_number", "phone_number"),
        Index("ix_patients_last_name", "last_name"),
    )


class CallTranscript(Base):
    __tablename__ = "call_transcripts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid4_str)
    call_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Nullable on purpose: a call that drops before confirmation produces a
    # transcript with no patient, and losing it would lose the interaction.
    patient_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("patients.patient_id"), nullable=True
    )
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

- [ ] **Step 4: Import the models where tables are created**

Add to `app/main.py`, directly below the existing imports:

```python
from app import models  # noqa: F401  -- registers tables on Base.metadata
from app.db import Base, engine
```

And directly below `install_exception_handlers(app)`:

```python
# No migration tool in this project. The schema is small and additive, and
# create_all is idempotent. A real deployment would use Alembic.
Base.metadata.create_all(bind=engine)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: PASS, 2 tests

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/main.py tests/test_models.py
git commit -m "feat: add patient and call transcript tables"
```

---

### Task 4: Schemas, validation, and spoken error text

**Files:**
- Create: `app/schemas.py`
- Test: `tests/test_schemas.py`

**Interfaces:**
- Consumes: nothing
- Produces: `SEX_VALUES: tuple[str, ...]`, `SPOKEN_ERRORS: dict[str, str]`, `PatientCreate`, `PatientUpdate`, `PatientOut`, `spoken_error_for(exc: ValidationError) -> str`

- [ ] **Step 1: Write the failing tests**

`tests/test_schemas.py`:

```python
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.schemas import PatientCreate, PatientUpdate, spoken_error_for

VALID = {
    "first_name": "Jane",
    "last_name": "Doe",
    "date_of_birth": "1992-01-05",
    "sex": "Female",
    "phone_number": "4155550142",
    "address_line_1": "1 Market St",
    "city": "San Francisco",
    "state": "CA",
    "zip_code": "94105",
}


def test_valid_payload_parses_and_defaults_language():
    patient = PatientCreate(**VALID)
    assert patient.date_of_birth == date(1992, 1, 5)
    assert patient.preferred_language == "English"


def test_future_date_of_birth_is_rejected_with_spoken_error():
    future = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError) as exc:
        PatientCreate(**{**VALID, "date_of_birth": future})
    assert "date of birth" in spoken_error_for(exc.value).lower()


def test_short_phone_number_is_rejected_with_spoken_error():
    with pytest.raises(ValidationError) as exc:
        PatientCreate(**{**VALID, "phone_number": "555"})
    assert "phone number" in spoken_error_for(exc.value).lower()


def test_invalid_state_and_zip_are_rejected():
    with pytest.raises(ValidationError):
        PatientCreate(**{**VALID, "state": "XX"})
    with pytest.raises(ValidationError):
        PatientCreate(**{**VALID, "zip_code": "941"})


def test_names_reject_digits_but_allow_hyphen_and_apostrophe():
    PatientCreate(**{**VALID, "last_name": "O'Brien-Smith"})
    with pytest.raises(ValidationError):
        PatientCreate(**{**VALID, "first_name": "Jane3"})


def test_area_code_may_not_start_with_zero_or_one():
    with pytest.raises(ValidationError):
        PatientCreate(**{**VALID, "phone_number": "1155550142"})


def test_missing_required_field_is_rejected():
    payload = {k: v for k, v in VALID.items() if k != "city"}
    with pytest.raises(ValidationError) as exc:
        PatientCreate(**payload)
    assert "city" in spoken_error_for(exc.value).lower()


def test_update_allows_partial_payload():
    update = PatientUpdate(city="Oakland")
    assert update.model_dump(exclude_unset=True) == {"city": "Oakland"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.schemas'`

- [ ] **Step 3: Write the schemas**

`app/schemas.py`:

```python
"""Validation rules and the text the voice agent speaks when one fails.

The spoken text lives here rather than in the system prompt on purpose. A prompt
instruction to "re-prompt for invalid fields" drifts; a tool result that says
exactly what went wrong does not. The voice layer maps a ValidationError to one
of these strings and hands it back to the model as the tool's output.
"""

import re
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, StringConstraints, field_validator
from pydantic import ValidationError

SEX_VALUES = ("Male", "Female", "Other", "Decline to Answer")

STATE_ABBREVIATIONS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z '\-]{0,49}$")
ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
NANP_RE = re.compile(r"^[2-9]\d{2}[2-9]\d{6}$")
MAX_AGE_YEARS = 130

SPOKEN_ERRORS: dict[str, str] = {
    "first_name": "I did not catch a usable first name. Please ask the caller to say and, if needed, spell their first name.",
    "last_name": "I did not catch a usable last name. Please ask the caller to say and, if needed, spell their last name.",
    "date_of_birth": "That date of birth is not valid. It cannot be in the future. Please ask the caller for their date of birth again, including the year.",
    "sex": "That is not one of the accepted options. Please ask whether the caller would like to say Male, Female, Other, or decline to answer.",
    "phone_number": "That phone number is not a valid ten digit US number. Please ask the caller to repeat their phone number including the area code.",
    "email": "That email address is not valid. Please ask the caller to repeat it slowly, or offer to skip it since it is optional.",
    "address_line_1": "I did not catch a usable street address. Please ask the caller for their street number and street name.",
    "city": "I did not catch a usable city. Please ask the caller for their city.",
    "state": "That is not a valid US state. Please ask the caller for their state.",
    "zip_code": "That ZIP code is not valid. Please ask the caller for their five digit ZIP code.",
    "emergency_contact_phone": "That emergency contact phone number is not a valid ten digit US number. Please ask the caller to repeat it.",
}

GENERIC_SPOKEN_ERROR = "Something about that information was not valid. Please ask the caller to repeat the last answer."

ShortText = Annotated[str, StringConstraints(min_length=1, max_length=100, strip_whitespace=True)]
AddressText = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]


def _validate_name(value: str) -> str:
    if not NAME_RE.match(value.strip()):
        raise ValueError("must be 1-50 letters, hyphens, or apostrophes")
    return value.strip()


def _validate_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if not NANP_RE.match(digits):
        raise ValueError("must be a valid 10-digit US phone number")
    return digits


def _validate_dob(value: date) -> date:
    today = date.today()
    if value > today:
        raise ValueError("cannot be in the future")
    if (today - value).days > MAX_AGE_YEARS * 366:
        raise ValueError("is implausibly far in the past")
    return value


class PatientBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: EmailStr | None = None
    address_line_1: AddressText
    address_line_2: Annotated[str, StringConstraints(max_length=200)] | None = None
    city: ShortText
    state: str
    zip_code: str
    insurance_provider: Annotated[str, StringConstraints(max_length=100)] | None = None
    insurance_member_id: Annotated[str, StringConstraints(max_length=50)] | None = None
    preferred_language: Annotated[str, StringConstraints(max_length=50)] = "English"
    emergency_contact_name: Annotated[str, StringConstraints(max_length=100)] | None = None
    emergency_contact_phone: str | None = None

    @field_validator("first_name", "last_name")
    @classmethod
    def check_name(cls, value: str) -> str:
        return _validate_name(value)

    @field_validator("date_of_birth")
    @classmethod
    def check_dob(cls, value: date) -> date:
        return _validate_dob(value)

    @field_validator("sex")
    @classmethod
    def check_sex(cls, value: str) -> str:
        if value not in SEX_VALUES:
            raise ValueError(f"must be one of {', '.join(SEX_VALUES)}")
        return value

    @field_validator("phone_number")
    @classmethod
    def check_phone(cls, value: str) -> str:
        return _validate_phone(value)

    @field_validator("emergency_contact_phone")
    @classmethod
    def check_emergency_phone(cls, value: str | None) -> str | None:
        return _validate_phone(value) if value else None

    @field_validator("state")
    @classmethod
    def check_state(cls, value: str) -> str:
        upper = value.strip().upper()
        if upper not in STATE_ABBREVIATIONS:
            raise ValueError("must be a valid 2-letter US state abbreviation")
        return upper

    @field_validator("zip_code")
    @classmethod
    def check_zip(cls, value: str) -> str:
        if not ZIP_RE.match(value.strip()):
            raise ValueError("must be a 5-digit or ZIP+4 US ZIP code")
        return value.strip()


class PatientCreate(PatientBase):
    pass


class PatientUpdate(BaseModel):
    """Every field optional. Callers send only what changed."""

    model_config = ConfigDict(str_strip_whitespace=True)

    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: date | None = None
    sex: str | None = None
    phone_number: str | None = None
    email: EmailStr | None = None
    address_line_1: AddressText | None = None
    address_line_2: Annotated[str, StringConstraints(max_length=200)] | None = None
    city: ShortText | None = None
    state: str | None = None
    zip_code: str | None = None
    insurance_provider: Annotated[str, StringConstraints(max_length=100)] | None = None
    insurance_member_id: Annotated[str, StringConstraints(max_length=50)] | None = None
    preferred_language: Annotated[str, StringConstraints(max_length=50)] | None = None
    emergency_contact_name: Annotated[str, StringConstraints(max_length=100)] | None = None
    emergency_contact_phone: str | None = None

    @field_validator("first_name", "last_name")
    @classmethod
    def check_name(cls, value: str | None) -> str | None:
        return _validate_name(value) if value else None

    @field_validator("date_of_birth")
    @classmethod
    def check_dob(cls, value: date | None) -> date | None:
        return _validate_dob(value) if value else None

    @field_validator("sex")
    @classmethod
    def check_sex(cls, value: str | None) -> str | None:
        if value and value not in SEX_VALUES:
            raise ValueError(f"must be one of {', '.join(SEX_VALUES)}")
        return value

    @field_validator("phone_number", "emergency_contact_phone")
    @classmethod
    def check_phone(cls, value: str | None) -> str | None:
        return _validate_phone(value) if value else None

    @field_validator("state")
    @classmethod
    def check_state(cls, value: str | None) -> str | None:
        if value is None:
            return None
        upper = value.strip().upper()
        if upper not in STATE_ABBREVIATIONS:
            raise ValueError("must be a valid 2-letter US state abbreviation")
        return upper

    @field_validator("zip_code")
    @classmethod
    def check_zip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not ZIP_RE.match(value.strip()):
            raise ValueError("must be a 5-digit or ZIP+4 US ZIP code")
        return value.strip()


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    patient_id: str
    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: str | None
    address_line_1: str
    address_line_2: str | None
    city: str
    state: str
    zip_code: str
    insurance_provider: str | None
    insurance_member_id: str | None
    preferred_language: str
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    created_at: datetime
    updated_at: datetime


def spoken_error_for(exc: ValidationError) -> str:
    """Turn the first validation failure into one sentence the agent can say."""
    errors = exc.errors()
    if not errors:
        return GENERIC_SPOKEN_ERROR
    location = errors[0].get("loc") or ()
    field = str(location[0]) if location else ""
    if errors[0].get("type") == "missing" and field:
        pretty = field.replace("_", " ")
        return f"The {pretty} is required and was not provided. Please ask the caller for their {pretty}."
    return SPOKEN_ERRORS.get(field, GENERIC_SPOKEN_ERROR)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_schemas.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add app/schemas.py tests/test_schemas.py
git commit -m "feat: add patient schemas with validation and spoken error text"
```

---

### Task 5: Patient service layer

**Files:**
- Create: `app/services/__init__.py`, `app/services/patients.py`
- Test: `tests/test_services.py`

**Interfaces:**
- Consumes: `app.models.Patient`, `app.models.CallTranscript`
- Produces: `create_patient(db, data: dict) -> Patient`, `get_patient(db, patient_id: str) -> Patient | None`, `list_patients(db, last_name=None, date_of_birth=None, phone_number=None) -> list[Patient]`, `update_patient(db, patient_id: str, data: dict) -> Patient | None`, `soft_delete_patient(db, patient_id: str) -> Patient | None`, `find_by_phone(db, phone_number: str) -> Patient | None`, `find_recent_duplicate(db, phone_number: str, date_of_birth: date, window_minutes: int = 5) -> Patient | None`, `save_transcript(db, call_id: str, transcript: str | None, summary: str | None, patient_id: str | None = None) -> CallTranscript`

- [ ] **Step 1: Write the failing tests**

`tests/test_services.py`:

```python
from datetime import date, datetime, timedelta, timezone

from app.models import Patient
from app.services import patients as service

BASE = {
    "first_name": "Jane",
    "last_name": "Doe",
    "date_of_birth": date(1992, 1, 5),
    "sex": "Female",
    "phone_number": "4155550142",
    "address_line_1": "1 Market St",
    "city": "San Francisco",
    "state": "CA",
    "zip_code": "94105",
}


def test_create_and_get_round_trip(db):
    created = service.create_patient(db, BASE)
    fetched = service.get_patient(db, created.patient_id)
    assert fetched is not None
    assert fetched.first_name == "Jane"


def test_soft_deleted_patient_is_invisible_to_get_and_list(db):
    created = service.create_patient(db, BASE)
    service.soft_delete_patient(db, created.patient_id)

    assert service.get_patient(db, created.patient_id) is None
    assert service.list_patients(db) == []
    # The row itself is still there.
    assert db.get(Patient, created.patient_id) is not None


def test_list_filters_by_last_name_phone_and_dob(db):
    service.create_patient(db, BASE)
    service.create_patient(db, {**BASE, "last_name": "Smith", "phone_number": "4155550199"})

    assert len(service.list_patients(db, last_name="Doe")) == 1
    assert len(service.list_patients(db, phone_number="4155550199")) == 1
    assert len(service.list_patients(db, date_of_birth=date(1992, 1, 5))) == 2
    assert len(service.list_patients(db, last_name="Nobody")) == 0


def test_update_applies_partial_changes_only(db):
    created = service.create_patient(db, BASE)
    updated = service.update_patient(db, created.patient_id, {"city": "Oakland"})
    assert updated.city == "Oakland"
    assert updated.first_name == "Jane"


def test_update_and_delete_return_none_for_unknown_id(db):
    assert service.update_patient(db, "missing", {"city": "Oakland"}) is None
    assert service.soft_delete_patient(db, "missing") is None


def test_find_by_phone_ignores_soft_deleted(db):
    created = service.create_patient(db, BASE)
    assert service.find_by_phone(db, "4155550142") is not None
    service.soft_delete_patient(db, created.patient_id)
    assert service.find_by_phone(db, "4155550142") is None


def test_recent_duplicate_found_inside_window_and_not_outside(db):
    created = service.create_patient(db, BASE)
    assert service.find_recent_duplicate(db, "4155550142", date(1992, 1, 5)) is not None

    created.created_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    db.commit()
    assert service.find_recent_duplicate(db, "4155550142", date(1992, 1, 5)) is None


def test_save_transcript_without_patient(db):
    record = service.save_transcript(db, "call-1", "hello", "greeting")
    assert record.patient_id is None
    assert record.call_id == "call-1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_services.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services'`

- [ ] **Step 3: Write the service layer**

`app/services/__init__.py`: empty file.

`app/services/patients.py`:

```python
"""All database access lives here. Both the REST router and the voice webhook
call these functions, so validation and soft-delete semantics cannot drift
between the phone path and the API path.

Nothing in this module imports from FastAPI. It takes plain Python in and
returns ORM objects out, which is what makes it testable without a web server.
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CallTranscript, Patient


def _active(statement):
    return statement.where(Patient.deleted_at.is_(None))


def create_patient(db: Session, data: dict) -> Patient:
    patient = Patient(**data)
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


def get_patient(db: Session, patient_id: str) -> Patient | None:
    statement = _active(select(Patient).where(Patient.patient_id == patient_id))
    return db.execute(statement).scalar_one_or_none()


def list_patients(
    db: Session,
    last_name: str | None = None,
    date_of_birth: date | None = None,
    phone_number: str | None = None,
) -> list[Patient]:
    statement = _active(select(Patient))
    if last_name:
        statement = statement.where(Patient.last_name == last_name)
    if date_of_birth:
        statement = statement.where(Patient.date_of_birth == date_of_birth)
    if phone_number:
        statement = statement.where(Patient.phone_number == phone_number)
    statement = statement.order_by(Patient.created_at.desc())
    return list(db.execute(statement).scalars().all())


def update_patient(db: Session, patient_id: str, data: dict) -> Patient | None:
    patient = get_patient(db, patient_id)
    if patient is None:
        return None
    for key, value in data.items():
        setattr(patient, key, value)
    db.commit()
    db.refresh(patient)
    return patient


def soft_delete_patient(db: Session, patient_id: str) -> Patient | None:
    patient = get_patient(db, patient_id)
    if patient is None:
        return None
    patient.deleted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(patient)
    return patient


def find_by_phone(db: Session, phone_number: str) -> Patient | None:
    statement = (
        _active(select(Patient).where(Patient.phone_number == phone_number))
        .order_by(Patient.created_at.desc())
        .limit(1)
    )
    return db.execute(statement).scalar_one_or_none()


def find_recent_duplicate(
    db: Session, phone_number: str, date_of_birth: date, window_minutes: int = 5
) -> Patient | None:
    """Guards against the agent retrying a save after a slow or ambiguous
    response. Same phone and date of birth inside the window is the same
    registration, not a second one."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    statement = _active(
        select(Patient).where(
            Patient.phone_number == phone_number,
            Patient.date_of_birth == date_of_birth,
            Patient.created_at >= cutoff,
        )
    ).order_by(Patient.created_at.desc()).limit(1)
    return db.execute(statement).scalar_one_or_none()


def save_transcript(
    db: Session,
    call_id: str,
    transcript: str | None,
    summary: str | None,
    patient_id: str | None = None,
) -> CallTranscript:
    record = CallTranscript(
        call_id=call_id, transcript=transcript, summary=summary, patient_id=patient_id
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_services.py -v`
Expected: PASS, 8 tests

Note: if `test_recent_duplicate_found_inside_window_and_not_outside` fails comparing a naive to an aware datetime, SQLite has returned a naive value. Coerce inside `find_recent_duplicate` by comparing against `cutoff.replace(tzinfo=None)` when `Patient.created_at` comes back naive — or simpler, keep the comparison in SQL as written, which is what this implementation does.

- [ ] **Step 5: Commit**

```bash
git add app/services tests/test_services.py
git commit -m "feat: add patient service layer shared by REST and voice paths"
```

---

### Task 6: REST API

**Files:**
- Create: `app/api/__init__.py`, `app/api/patients.py`
- Modify: `app/main.py`
- Test: `tests/test_patients_api.py`

**Interfaces:**
- Consumes: `app.schemas.PatientCreate`, `PatientUpdate`, `PatientOut`; `app.services.patients` functions; `app.envelope.ok`, `error_body`
- Produces: `app.api.patients.router` mounted at `/patients`

- [ ] **Step 1: Write the failing tests**

`tests/test_patients_api.py`:

```python
VALID = {
    "first_name": "Jane",
    "last_name": "Doe",
    "date_of_birth": "1992-01-05",
    "sex": "Female",
    "phone_number": "4155550142",
    "address_line_1": "1 Market St",
    "city": "San Francisco",
    "state": "CA",
    "zip_code": "94105",
}


def test_create_returns_201_with_envelope_and_uuid(client):
    response = client.post("/patients", json=VALID)
    assert response.status_code == 201
    body = response.json()
    assert body["error"] is None
    assert len(body["data"]["patient_id"]) == 36
    assert body["data"]["preferred_language"] == "English"


def test_create_with_invalid_field_returns_422_envelope(client):
    response = client.post("/patients", json={**VALID, "phone_number": "555"})
    assert response.status_code == 422
    body = response.json()
    assert body["data"] is None
    assert body["error"]["code"] == "validation_error"


def test_get_unknown_id_returns_404_envelope(client):
    response = client.get("/patients/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_error"


def test_list_filters_by_last_name(client):
    client.post("/patients", json=VALID)
    client.post("/patients", json={**VALID, "last_name": "Smith", "phone_number": "4155550199"})

    response = client.get("/patients", params={"last_name": "Doe"})
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1


def test_put_applies_partial_update(client):
    created = client.post("/patients", json=VALID).json()["data"]
    response = client.put(f"/patients/{created['patient_id']}", json={"city": "Oakland"})
    assert response.status_code == 200
    assert response.json()["data"]["city"] == "Oakland"
    assert response.json()["data"]["first_name"] == "Jane"


def test_delete_is_soft_and_hides_the_record(client):
    created = client.post("/patients", json=VALID).json()["data"]
    assert client.delete(f"/patients/{created['patient_id']}").status_code == 200
    assert client.get(f"/patients/{created['patient_id']}").status_code == 404
    assert client.get("/patients").json()["data"] == []


def test_list_rejects_unparseable_date_filter(client):
    response = client.get("/patients", params={"date_of_birth": "banana"})
    assert response.status_code == 400
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_patients_api.py -v`
Expected: FAIL with 404 on every route

- [ ] **Step 3: Write the REST router**

`app/api/__init__.py`: empty file.

`app/api/patients.py`:

```python
import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.envelope import ok
from app.normalizers import normalize_date
from app.schemas import PatientCreate, PatientOut, PatientUpdate
from app.services import patients as service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/patients", tags=["patients"])


def _serialize(patient) -> dict:
    return PatientOut.model_validate(patient).model_dump(mode="json")


@router.get("")
def list_patients(
    response: Response,
    last_name: str | None = None,
    date_of_birth: str | None = None,
    phone_number: str | None = None,
    db: Session = Depends(get_db),
):
    parsed_dob = None
    if date_of_birth:
        parsed_dob = normalize_date(date_of_birth)
        if parsed_dob is None:
            raise HTTPException(status_code=400, detail="date_of_birth must be a valid date")

    records = service.list_patients(
        db, last_name=last_name, date_of_birth=parsed_dob, phone_number=phone_number
    )
    return ok([_serialize(record) for record in records])


@router.get("/{patient_id}")
def get_patient(patient_id: str, db: Session = Depends(get_db)):
    patient = service.get_patient(db, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return ok(_serialize(patient))


@router.post("", status_code=201)
def create_patient(payload: PatientCreate, db: Session = Depends(get_db)):
    patient = service.create_patient(db, payload.model_dump())
    logger.info("Created patient %s via REST API", patient.patient_id)
    return ok(_serialize(patient))


@router.put("/{patient_id}")
def update_patient(patient_id: str, payload: PatientUpdate, db: Session = Depends(get_db)):
    changes = payload.model_dump(exclude_unset=True)
    patient = service.update_patient(db, patient_id, changes)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    logger.info("Updated patient %s fields=%s", patient_id, sorted(changes))
    return ok(_serialize(patient))


@router.delete("/{patient_id}")
def delete_patient(patient_id: str, db: Session = Depends(get_db)):
    patient = service.soft_delete_patient(db, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    logger.info("Soft-deleted patient %s", patient_id)
    return ok({"patient_id": patient_id, "deleted_at": patient.deleted_at.isoformat()})
```

- [ ] **Step 4: Mount the router**

Add to `app/main.py` imports:

```python
from app.api import patients as patients_api
```

And after `Base.metadata.create_all(bind=engine)`:

```python
app.include_router(patients_api.router)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_patients_api.py -v`
Expected: PASS, 7 tests

- [ ] **Step 6: Commit**

```bash
git add app/api tests/test_patients_api.py app/main.py
git commit -m "feat: add REST patients API with envelope and soft delete"
```

---

### Task 7: Vapi voice webhook

**Files:**
- Create: `app/api/voice.py`
- Modify: `app/main.py`
- Test: `tests/test_voice_webhook.py`

**Interfaces:**
- Consumes: `app.normalizers.normalize_patient_payload`, `app.schemas.PatientCreate`, `PatientUpdate`, `spoken_error_for`, `app.services.patients` functions
- Produces: `app.api.voice.router` mounted at `/voice`, handling `POST /voice/webhook`. Tool names accepted: `lookup_patient_by_phone`, `register_patient`, `update_patient`.

- [ ] **Step 1: Write the failing tests**

`tests/test_voice_webhook.py`:

```python
VALID_ARGS = {
    "first_name": "Jane",
    "last_name": "Doe",
    "date_of_birth": "01/05/1992",
    "sex": "female",
    "phone_number": "+1 (415) 555-0142",
    "address_line_1": "1 Market St",
    "city": "San Francisco",
    "state": "California",
    "zip_code": "94105",
}

HEADERS = {"x-vapi-secret": "test-secret"}


def tool_call(name: str, arguments: dict, call_id: str = "call-1") -> dict:
    return {
        "message": {
            "type": "tool-calls",
            "call": {"id": call_id},
            "toolCallList": [{"id": "tc-1", "name": name, "arguments": arguments}],
        }
    }


def test_webhook_rejects_wrong_secret(client):
    response = client.post(
        "/voice/webhook", json=tool_call("register_patient", VALID_ARGS),
        headers={"x-vapi-secret": "wrong"},
    )
    assert response.status_code == 401


def test_register_normalizes_loose_input_and_persists(client):
    response = client.post("/voice/webhook", json=tool_call("register_patient", VALID_ARGS), headers=HEADERS)
    assert response.status_code == 200
    assert "tc-1" == response.json()["results"][0]["toolCallId"]

    listed = client.get("/patients").json()["data"]
    assert len(listed) == 1
    assert listed[0]["state"] == "CA"
    assert listed[0]["phone_number"] == "4155550142"
    assert listed[0]["date_of_birth"] == "1992-01-05"


def test_register_with_future_dob_returns_spoken_error_and_saves_nothing(client):
    bad = {**VALID_ARGS, "date_of_birth": "01/05/2999"}
    response = client.post("/voice/webhook", json=tool_call("register_patient", bad), headers=HEADERS)

    result = response.json()["results"][0]["result"]
    assert "date of birth" in result.lower()
    assert client.get("/patients").json()["data"] == []


def test_register_twice_does_not_create_a_duplicate(client):
    client.post("/voice/webhook", json=tool_call("register_patient", VALID_ARGS), headers=HEADERS)
    client.post("/voice/webhook", json=tool_call("register_patient", VALID_ARGS), headers=HEADERS)
    assert len(client.get("/patients").json()["data"]) == 1


def test_lookup_reports_no_match_then_a_match(client):
    args = {"phone_number": "4155550142"}
    first = client.post("/voice/webhook", json=tool_call("lookup_patient_by_phone", args), headers=HEADERS)
    assert "no existing" in first.json()["results"][0]["result"].lower()

    client.post("/voice/webhook", json=tool_call("register_patient", VALID_ARGS), headers=HEADERS)
    second = client.post("/voice/webhook", json=tool_call("lookup_patient_by_phone", args), headers=HEADERS)
    result = second.json()["results"][0]["result"]
    assert "Jane" in result and "Doe" in result


def test_update_tool_changes_a_field(client):
    client.post("/voice/webhook", json=tool_call("register_patient", VALID_ARGS), headers=HEADERS)
    patient_id = client.get("/patients").json()["data"][0]["patient_id"]

    client.post(
        "/voice/webhook",
        json=tool_call("update_patient", {"patient_id": patient_id, "city": "Oakland"}),
        headers=HEADERS,
    )
    assert client.get(f"/patients/{patient_id}").json()["data"]["city"] == "Oakland"


def test_unknown_tool_name_returns_a_spoken_message(client):
    response = client.post("/voice/webhook", json=tool_call("do_something_else", {}), headers=HEADERS)
    assert response.status_code == 200
    assert "not available" in response.json()["results"][0]["result"].lower()


def test_openai_style_tool_call_shape_is_accepted(client):
    payload = {
        "message": {
            "type": "tool-calls",
            "call": {"id": "call-9"},
            "toolCalls": [
                {"id": "tc-9", "function": {"name": "lookup_patient_by_phone",
                                            "arguments": '{"phone_number": "4155550142"}'}}
            ],
        }
    }
    response = client.post("/voice/webhook", json=payload, headers=HEADERS)
    assert response.json()["results"][0]["toolCallId"] == "tc-9"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_voice_webhook.py -v`
Expected: FAIL with 404 on `/voice/webhook`

- [ ] **Step 3: Write the voice webhook**

`app/api/voice.py`:

```python
"""Adapter between Vapi and the patient service.

Vapi posts every server message for an assistant to one URL, discriminated by
message.type, so this module exposes a single endpoint and dispatches inside.

Tool results are returned as plain sentences rather than JSON. Vapi feeds the
result string back to the model as the tool's output, so a sentence that states
exactly what went wrong makes the agent re-prompt for the right field without
any re-prompting rules in the system prompt.
"""

import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.normalizers import normalize_patient_payload
from app.schemas import PatientCreate, PatientUpdate, spoken_error_for
from app.services import patients as service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice", tags=["voice"])

SAVE_FAILED = (
    "I could not save the record just now because of a system error. "
    "Apologise to the caller, tell them their details were not lost, and say "
    "the clinic will follow up."
)


def verify_secret(x_vapi_secret: str | None = Header(default=None)) -> None:
    """Vapi sends the assistant's configured server.secret on every request."""
    if settings.vapi_secret and x_vapi_secret != settings.vapi_secret:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


def _extract_tool_calls(message: dict) -> list[dict]:
    """Vapi sends a flattened toolCallList and an OpenAI-shaped toolCalls.
    Accept either so a change in the platform's payload does not break intake."""
    calls = []
    for item in message.get("toolCallList") or []:
        calls.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "arguments": item.get("arguments") or {},
        })
    if calls:
        return calls
    for item in message.get("toolCalls") or []:
        function = item.get("function") or {}
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        calls.append({"id": item.get("id"), "name": function.get("name"), "arguments": arguments})
    return calls


def _handle_lookup(db: Session, arguments: dict) -> str:
    phone = normalize_patient_payload(arguments).get("phone_number")
    if not phone:
        return "That phone number was not usable. Please ask the caller to repeat it."

    existing = service.find_by_phone(db, phone)
    if existing is None:
        return "There is no existing record for that number. Continue with a new registration."
    return (
        f"A record already exists for {existing.first_name} {existing.last_name}, "
        f"patient id {existing.patient_id}. Tell the caller you found their record and ask "
        "whether they would like to update it instead of creating a new one."
    )


def _handle_register(db: Session, arguments: dict) -> str:
    normalized = normalize_patient_payload(arguments)
    try:
        patient_data = PatientCreate(**normalized)
    except ValidationError as exc:
        logger.info("Rejected registration: %s", exc.errors())
        return spoken_error_for(exc)

    existing = service.find_recent_duplicate(
        db, patient_data.phone_number, patient_data.date_of_birth
    )
    if existing is not None:
        logger.info("Suppressed duplicate save for %s", existing.patient_id)
        return (
            f"That registration was already saved. The patient id is {existing.patient_id}. "
            f"Confirm to the caller that they are all set, {existing.first_name}."
        )

    try:
        patient = service.create_patient(db, patient_data.model_dump())
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database write failed. Collected payload: %s", normalized)
        return SAVE_FAILED

    logger.info("Registered patient %s payload=%s", patient.patient_id, patient_data.model_dump(mode="json"))
    return (
        f"The registration was saved successfully. The patient id is {patient.patient_id}. "
        f"Tell the caller they are all set, {patient.first_name}, and end the call politely."
    )


def _handle_update(db: Session, arguments: dict) -> str:
    patient_id = arguments.get("patient_id")
    if not patient_id:
        return "I need the patient id before I can update a record. Look the caller up by phone number first."

    changes = {k: v for k, v in arguments.items() if k != "patient_id" and v is not None}
    if not changes:
        return "No fields were provided to update. Please ask the caller what they would like to change."

    try:
        validated = PatientUpdate(**normalize_patient_payload(changes))
    except ValidationError as exc:
        logger.info("Rejected update: %s", exc.errors())
        return spoken_error_for(exc)

    try:
        patient = service.update_patient(db, patient_id, validated.model_dump(exclude_unset=True))
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database update failed. Payload: %s", changes)
        return SAVE_FAILED

    if patient is None:
        return "I could not find that record. Please look the caller up by phone number again."

    logger.info("Updated patient %s fields=%s", patient_id, sorted(changes))
    return (
        f"The record was updated successfully. Confirm the change to the caller, "
        f"{patient.first_name}, and ask whether anything else needs correcting."
    )


HANDLERS = {
    "lookup_patient_by_phone": _handle_lookup,
    "register_patient": _handle_register,
    "update_patient": _handle_update,
}


@router.post("/webhook", dependencies=[Depends(verify_secret)])
async def webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    message = body.get("message") or {}
    message_type = message.get("type")

    if message_type == "tool-calls":
        results = []
        for call in _extract_tool_calls(message):
            handler = HANDLERS.get(call["name"])
            if handler is None:
                logger.warning("Unknown tool requested: %s", call["name"])
                result = "That action is not available. Continue the conversation without it."
            else:
                result = handler(db, call["arguments"])
            results.append({"toolCallId": call["id"], "result": result})
        return {"results": results}

    if message_type == "end-of-call-report":
        return _handle_end_of_call(db, message)

    # Vapi sends status updates, speech events and transcripts to the same URL.
    # Acknowledge them so it does not retry.
    return {"received": True}


def _handle_end_of_call(db: Session, message: dict) -> dict:
    """Implemented in Task 8."""
    return {"received": True}
```

- [ ] **Step 4: Mount the router**

Add to `app/main.py` imports:

```python
from app.api import voice as voice_api
```

And after the patients router line:

```python
app.include_router(voice_api.router)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_voice_webhook.py -v`
Expected: PASS, 8 tests

- [ ] **Step 6: Commit**

```bash
git add app/api/voice.py tests/test_voice_webhook.py app/main.py
git commit -m "feat: add Vapi tool webhook with spoken validation errors"
```

---

### Task 8: End-of-call transcript capture

**Files:**
- Modify: `app/api/voice.py` (replace `_handle_end_of_call`)
- Test: `tests/test_call_reports.py`

**Interfaces:**
- Consumes: `app.services.patients.save_transcript`, `find_by_phone`
- Produces: `_handle_end_of_call(db, message: dict) -> dict` writing a `CallTranscript`

- [ ] **Step 1: Write the failing tests**

`tests/test_call_reports.py`:

```python
from app.models import CallTranscript

HEADERS = {"x-vapi-secret": "test-secret"}


def report(call_id="call-1", transcript="Agent: hello", summary="Registered a patient", customer_number=None):
    message = {
        "type": "end-of-call-report",
        "call": {"id": call_id},
        "artifact": {"transcript": transcript},
        "analysis": {"summary": summary},
    }
    if customer_number:
        message["call"]["customer"] = {"number": customer_number}
    return {"message": message}


def test_report_is_stored_even_with_no_patient(client, db):
    response = client.post("/voice/webhook", json=report(), headers=HEADERS)
    assert response.status_code == 200

    stored = db.query(CallTranscript).all()
    assert len(stored) == 1
    assert stored[0].patient_id is None
    assert stored[0].summary == "Registered a patient"


def test_report_links_to_patient_when_caller_number_matches(client, db):
    client.post("/patients", json={
        "first_name": "Jane", "last_name": "Doe", "date_of_birth": "1992-01-05",
        "sex": "Female", "phone_number": "4155550142", "address_line_1": "1 Market St",
        "city": "San Francisco", "state": "CA", "zip_code": "94105",
    })

    client.post("/voice/webhook", json=report(customer_number="+14155550142"), headers=HEADERS)

    stored = db.query(CallTranscript).one()
    assert stored.patient_id is not None


def test_report_without_call_id_is_acknowledged_and_ignored(client, db):
    payload = {"message": {"type": "end-of-call-report", "artifact": {"transcript": "x"}}}
    assert client.post("/voice/webhook", json=payload, headers=HEADERS).status_code == 200
    assert db.query(CallTranscript).count() == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_call_reports.py -v`
Expected: FAIL, no transcript rows written

- [ ] **Step 3: Replace the stub handler**

In `app/api/voice.py`, add `normalize_phone` to the normalizers import:

```python
from app.normalizers import normalize_patient_payload, normalize_phone
```

Then replace the `_handle_end_of_call` stub with:

```python
def _handle_end_of_call(db: Session, message: dict) -> dict:
    """Stores a transcript for every completed call, including calls that dropped
    before the caller confirmed. Those produce a transcript with no patient
    attached, which is the point: the interaction is not lost."""
    call = message.get("call") or {}
    call_id = call.get("id")
    if not call_id:
        logger.warning("End-of-call report with no call id; ignoring")
        return {"received": True}

    artifact = message.get("artifact") or {}
    analysis = message.get("analysis") or {}
    transcript = artifact.get("transcript") or message.get("transcript")
    summary = analysis.get("summary") or message.get("summary")

    patient_id = None
    caller_number = normalize_phone((call.get("customer") or {}).get("number"))
    if caller_number:
        existing = service.find_by_phone(db, caller_number)
        if existing is not None:
            patient_id = existing.patient_id

    try:
        service.save_transcript(db, call_id, transcript, summary, patient_id)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to store transcript for call %s", call_id)
        return {"received": True}

    logger.info("Stored transcript for call %s linked to patient %s", call_id, patient_id)
    return {"received": True}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_call_reports.py -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Run the whole suite**

Run: `pytest -v`
Expected: PASS, all tests

- [ ] **Step 6: Commit**

```bash
git add app/api/voice.py tests/test_call_reports.py
git commit -m "feat: store call transcripts and link them to patients by phone"
```

---

### Task 9: Dashboard

**Files:**
- Create: `app/static/index.html`
- Modify: `app/main.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `GET /patients`
- Produces: `GET /dashboard` serving the page

- [ ] **Step 1: Write the failing test**

`tests/test_dashboard.py`:

```python
def test_dashboard_is_served(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Patient Registrations" in response.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_dashboard.py -v`
Expected: FAIL with 404

- [ ] **Step 3: Write the dashboard page**

`app/static/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Patient Registrations</title>
<style>
  :root {
    --bg: #ffffff; --fg: #16181d; --muted: #5c6370;
    --line: #e3e6ea; --accent: #0b6e5f; --chip: #eef4f2;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #14161a; --fg: #e8eaed; --muted: #9aa3ad;
      --line: #2a2e36; --accent: #5fd0b8; --chip: #1d2b28;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 32px 16px; background: var(--bg); color: var(--fg);
    font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  main { max-width: 1100px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  p.sub { color: var(--muted); margin: 0 0 24px; }
  .bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
  input, button {
    font: inherit; padding: 8px 12px; border-radius: 8px;
    border: 1px solid var(--line); background: var(--bg); color: var(--fg);
  }
  button { background: var(--accent); color: #fff; border-color: transparent; cursor: pointer; }
  .count { display: inline-block; padding: 2px 10px; border-radius: 999px;
           background: var(--chip); color: var(--accent); font-size: 13px; }
  .scroll { overflow-x: auto; border: 1px solid var(--line); border-radius: 10px; }
  table { border-collapse: collapse; width: 100%; min-width: 900px; }
  th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--line);
           white-space: nowrap; }
  th { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
  tr:last-child td { border-bottom: none; }
  .empty, .error { padding: 32px; text-align: center; color: var(--muted); }
  .error { color: #c0392b; }
</style>
</head>
<body>
<main>
  <h1>Patient Registrations</h1>
  <p class="sub">Records collected by the voice agent. <span id="count" class="count">loading</span></p>

  <div class="bar">
    <input id="q" type="search" placeholder="Filter by last name" aria-label="Filter by last name">
    <button id="go">Search</button>
    <button id="all">Show all</button>
  </div>

  <div class="scroll">
    <table>
      <thead>
        <tr>
          <th>Name</th><th>DOB</th><th>Sex</th><th>Phone</th><th>Email</th>
          <th>Address</th><th>City</th><th>State</th><th>ZIP</th>
          <th>Insurance</th><th>Member ID</th><th>Language</th><th>Registered</th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
  </div>
</main>

<script>
const rows = document.getElementById("rows");
const count = document.getElementById("count");

function cell(value) {
  const td = document.createElement("td");
  td.textContent = value === null || value === undefined || value === "" ? "—" : value;
  return td;
}

function message(text, className) {
  rows.innerHTML = "";
  const tr = document.createElement("tr");
  const td = document.createElement("td");
  td.colSpan = 13;
  td.className = className;
  td.textContent = text;
  tr.appendChild(td);
  rows.appendChild(tr);
}

async function load(lastName) {
  const url = lastName ? `/patients?last_name=${encodeURIComponent(lastName)}` : "/patients";
  try {
    const response = await fetch(url);
    const body = await response.json();
    if (body.error) { throw new Error(body.error.message); }

    const patients = body.data;
    count.textContent = `${patients.length} record${patients.length === 1 ? "" : "s"}`;
    if (patients.length === 0) { message("No patients registered yet.", "empty"); return; }

    rows.innerHTML = "";
    for (const p of patients) {
      const tr = document.createElement("tr");
      [
        `${p.first_name} ${p.last_name}`, p.date_of_birth, p.sex, p.phone_number, p.email,
        [p.address_line_1, p.address_line_2].filter(Boolean).join(", "),
        p.city, p.state, p.zip_code, p.insurance_provider, p.insurance_member_id,
        p.preferred_language, new Date(p.created_at).toLocaleString(),
      ].forEach(v => tr.appendChild(cell(v)));
      rows.appendChild(tr);
    }
  } catch (err) {
    count.textContent = "error";
    message(`Could not load patients: ${err.message}`, "error");
  }
}

document.getElementById("go").onclick = () => load(document.getElementById("q").value.trim());
document.getElementById("all").onclick = () => {
  document.getElementById("q").value = "";
  load("");
};
document.getElementById("q").addEventListener("keydown", e => {
  if (e.key === "Enter") { load(e.target.value.trim()); }
});

load("");
</script>
</body>
</html>
```

- [ ] **Step 4: Serve the page**

Add to `app/main.py` imports:

```python
from pathlib import Path

from fastapi.responses import FileResponse
```

And after the router includes:

```python
DASHBOARD = Path(__file__).parent / "static" / "index.html"


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(DASHBOARD)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_dashboard.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/static/index.html app/main.py tests/test_dashboard.py
git commit -m "feat: add patient dashboard served from the backend"
```

---

### Task 10: System prompt and Vapi assistant configuration

**Files:**
- Create: `prompts/system_prompt.md`, `vapi/assistant.json`, `scripts/push_assistant.py`

**Interfaces:**
- Consumes: the tool names implemented in Task 7
- Produces: a committed, reviewable assistant configuration and a script that pushes it

- [ ] **Step 1: Write the system prompt**

`prompts/system_prompt.md`:

```markdown
# Patient Intake Agent — System Prompt

You are the patient intake coordinator for Huzaifa Healthcare. You speak with
people on the phone who want to register as new patients. You are warm,
efficient, and clear. You sound like a competent human receptionist, never like
a form being read aloud.

## How to speak

- One question at a time. Never read a list of fields at the caller.
- Short sentences. This is spoken, not written.
- Never say field names like "address line 1". Say "what is your street address".
- Never spell out or pronounce punctuation, and never read the patient id aloud
  unless the caller asks for it.
- If the caller gives several answers at once, accept all of them and skip ahead.
  Do not ask again for something you already have.
- If the caller corrects you, accept the correction immediately and without
  comment beyond a brief "got it".
- If the caller asks to start over, discard everything you have collected and
  begin again from the first question.
- If the caller asks a medical question, say you cannot give medical advice and
  that a clinician will follow up.
- If the caller describes an emergency, tell them to hang up and dial 911.

## What to collect

Required, in roughly this order:

1. First name and last name. If a name is unusual, ask them to spell it.
2. Date of birth, including the year.
3. Sex: Male, Female, Other, or they may decline to answer.
4. Phone number, ten digits.
5. Street address, then city, then state, then ZIP code.

Then offer the optional information exactly once, as a single question:

> "I can also take your insurance details, an emergency contact, and your
> preferred language. Would you like to add any of those?"

If they say yes, collect only what they offer. If they say no, move on. Never
press. Email is optional as well — offer it with the others, and do not insist.

## Tools

You have three tools.

**`lookup_patient_by_phone`** — Call this as soon as you have the caller's phone
number, before collecting anything else beyond their name. If it reports an
existing record, tell the caller you found them and ask whether they would like
to update their existing information instead of creating a new record.

**`register_patient`** — Call this only after you have read every collected field
back to the caller and they have confirmed it is correct.

**`update_patient`** — Call this when a returning caller wants to change their
existing record. You must have a patient id from `lookup_patient_by_phone`
first.

## Confirmation is mandatory

Before calling `register_patient`, read back everything you collected in a
natural sentence and ask the caller to confirm. For example:

> "Let me read that back. Jane Doe, born January fifth nineteen ninety-two,
> female, phone four one five, five five five, zero one four two, at one Market
> Street, San Francisco, California, nine four one zero five. Is all of that
> correct?"

Read digits individually for phone numbers and ZIP codes. If the caller corrects
anything, fix it and read back only the corrected part.

## Handling tool results

Tool results are instructions written for you. Follow them exactly.

If a tool tells you a value was invalid, ask the caller again for **only that
one field**. Do not restart, do not re-read everything, and do not explain the
technical reason. Just ask for that field again, naturally.

If a tool reports a system error, apologise briefly, tell the caller their
details were not lost and the clinic will follow up, then end the call politely.

## Ending the call

Once the record is saved, say something brief and warm — "You're all set,
Jane" — offer nothing further unless they ask, and end the call using your
`end_after_message` tool.
```

- [ ] **Step 2: Write the assistant configuration**

`vapi/assistant.json`. Replace `<YOUR-BACKEND-URL>` with the deployed base URL from Task 11.

```json
{
  "name": "Patient Intake Agent",
  "firstMessage": "Thanks for calling Huzaifa Healthcare, this is the patient registration line. Are you calling to register as a new patient?",
  "model": {
    "provider": "openai",
    "model": "gpt-4o",
    "temperature": 0.3,
    "messages": [
      {
        "role": "system",
        "content": "REPLACED AT PUSH TIME FROM prompts/system_prompt.md"
      }
    ],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "lookup_patient_by_phone",
          "description": "Check whether a patient record already exists for a phone number. Call this as soon as the caller gives their phone number.",
          "parameters": {
            "type": "object",
            "properties": {
              "phone_number": {
                "type": "string",
                "description": "The caller's US phone number, digits only if possible."
              }
            },
            "required": ["phone_number"]
          }
        },
        "server": { "url": "<YOUR-BACKEND-URL>/voice/webhook" }
      },
      {
        "type": "function",
        "function": {
          "name": "register_patient",
          "description": "Save a new patient record. Call this only after reading every field back to the caller and receiving their confirmation.",
          "parameters": {
            "type": "object",
            "properties": {
              "first_name": { "type": "string" },
              "last_name": { "type": "string" },
              "date_of_birth": { "type": "string", "description": "Any clear date format. MM/DD/YYYY is fine." },
              "sex": { "type": "string", "enum": ["Male", "Female", "Other", "Decline to Answer"] },
              "phone_number": { "type": "string", "description": "US phone number, 10 digits." },
              "email": { "type": "string" },
              "address_line_1": { "type": "string" },
              "address_line_2": { "type": "string" },
              "city": { "type": "string" },
              "state": { "type": "string", "description": "US state. Full name or two-letter abbreviation." },
              "zip_code": { "type": "string" },
              "insurance_provider": { "type": "string" },
              "insurance_member_id": { "type": "string" },
              "preferred_language": { "type": "string" },
              "emergency_contact_name": { "type": "string" },
              "emergency_contact_phone": { "type": "string" }
            },
            "required": [
              "first_name", "last_name", "date_of_birth", "sex", "phone_number",
              "address_line_1", "city", "state", "zip_code"
            ]
          }
        },
        "server": { "url": "<YOUR-BACKEND-URL>/voice/webhook" }
      },
      {
        "type": "function",
        "function": {
          "name": "update_patient",
          "description": "Update an existing patient record. Requires a patient_id from lookup_patient_by_phone. Send only the fields that are changing.",
          "parameters": {
            "type": "object",
            "properties": {
              "patient_id": { "type": "string" },
              "first_name": { "type": "string" },
              "last_name": { "type": "string" },
              "date_of_birth": { "type": "string" },
              "sex": { "type": "string", "enum": ["Male", "Female", "Other", "Decline to Answer"] },
              "phone_number": { "type": "string" },
              "email": { "type": "string" },
              "address_line_1": { "type": "string" },
              "address_line_2": { "type": "string" },
              "city": { "type": "string" },
              "state": { "type": "string" },
              "zip_code": { "type": "string" },
              "insurance_provider": { "type": "string" },
              "insurance_member_id": { "type": "string" },
              "preferred_language": { "type": "string" },
              "emergency_contact_name": { "type": "string" },
              "emergency_contact_phone": { "type": "string" }
            },
            "required": ["patient_id"]
          }
        },
        "server": { "url": "<YOUR-BACKEND-URL>/voice/webhook" }
      }
    ]
  },
  "voice": { "provider": "11labs", "voiceId": "burt" },
  "transcriber": { "provider": "deepgram", "model": "nova-2", "language": "en-US" },
  "server": { "url": "<YOUR-BACKEND-URL>/voice/webhook" },
  "serverMessages": ["tool-calls", "end-of-call-report"],
  "endCallMessage": "Thanks for calling. Take care.",
  "silenceTimeoutSeconds": 20,
  "maxDurationSeconds": 900
}
```

- [ ] **Step 3: Write the push script**

`scripts/push_assistant.py`:

```python
"""Push vapi/assistant.json to Vapi, with the system prompt inlined from
prompts/system_prompt.md.

Keeping the prompt in the repo rather than the Vapi dashboard is what makes it
reviewable in git. This script is the one-way sync.

Usage:
    VAPI_API_KEY=... VAPI_ASSISTANT_ID=... BACKEND_URL=https://... \
        python scripts/push_assistant.py
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    api_key = os.environ.get("VAPI_API_KEY")
    assistant_id = os.environ.get("VAPI_ASSISTANT_ID")
    backend_url = os.environ.get("BACKEND_URL", "").rstrip("/")

    missing = [
        name for name, value in
        (("VAPI_API_KEY", api_key), ("VAPI_ASSISTANT_ID", assistant_id), ("BACKEND_URL", backend_url))
        if not value
    ]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 1

    config = json.loads((ROOT / "vapi" / "assistant.json").read_text(encoding="utf-8"))
    prompt = (ROOT / "prompts" / "system_prompt.md").read_text(encoding="utf-8")
    config["model"]["messages"][0]["content"] = prompt

    raw = json.dumps(config).replace("<YOUR-BACKEND-URL>", backend_url)

    request = urllib.request.Request(
        f"https://api.vapi.ai/assistant/{assistant_id}",
        data=raw.encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(request) as response:
            print(f"Updated assistant {assistant_id} ({response.status})")
    except urllib.error.HTTPError as exc:
        print(f"Vapi rejected the update ({exc.code}): {exc.read().decode()}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Verify the JSON parses and the prompt inlines**

Run: `python -c "import json,pathlib; c=json.loads(pathlib.Path('vapi/assistant.json').read_text()); print(len(c['model']['tools']), 'tools')"`
Expected: `3 tools`

- [ ] **Step 5: Commit**

```bash
git add prompts vapi scripts
git commit -m "feat: commit system prompt, assistant config, and push script"
```

---

### Task 11: Deployment configuration and README

**Files:**
- Create: `render.yaml`, `README.md`
- Test: manual — a live call and a live API request

**Interfaces:**
- Consumes: everything above
- Produces: a deployed service and the submission artifacts

- [ ] **Step 1: Write the Render service definition**

`render.yaml`:

```yaml
services:
  - type: web
    name: voice-patient-intake
    runtime: python
    plan: free
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
    healthCheckPath: /health
    envVars:
      - key: DATABASE_URL
        sync: false
      - key: VAPI_SECRET
        sync: false
      - key: PYTHON_VERSION
        value: "3.12.7"
```

- [ ] **Step 2: Provision the database and deploy**

1. Create a Neon project. Copy the connection string.
2. Create a Render web service from the GitHub repository.
3. Set `DATABASE_URL` to the Neon string and `VAPI_SECRET` to a random value.
4. Deploy. Confirm `GET https://<service>.onrender.com/health` returns
   `{"data":{"status":"ok"},"error":null}`.

- [ ] **Step 3: Keep the free instance awake**

Create a free cron-job.org job that sends `GET https://<service>.onrender.com/health`
every 10 minutes. Without it the instance sleeps after 15 minutes and a call
arriving at a cold backend times out while the agent waits for the save.

- [ ] **Step 4: Push the assistant configuration**

```bash
VAPI_API_KEY=<private key> \
VAPI_ASSISTANT_ID=<assistant id> \
BACKEND_URL=https://<service>.onrender.com \
python scripts/push_assistant.py
```

Then in the Vapi dashboard set the assistant's **server secret** to the same
value as `VAPI_SECRET`, and confirm the free phone number's inbound assistant is
this assistant.

- [ ] **Step 5: Verify end to end**

1. Call the number. Register as a test patient. Deliberately give a bad date of
   birth once, and correct a name once, to exercise the re-prompt path.
2. `curl https://<service>.onrender.com/patients` — the record is there.
3. Call again from the same number — the agent recognises the returning caller.
4. Open `https://<service>.onrender.com/dashboard` — the record is listed.

- [ ] **Step 6: Write the README**

`README.md` must contain, in this order:

1. **What this is** — one paragraph, plus the live phone number, API base URL,
   and dashboard URL.
2. **Architecture** — the diagram from the spec, and the reason the voice
   webhook and the REST router share `services/patients.py` rather than the
   backend calling itself over HTTP.
3. **Tech stack and why** — Vapi (free US inbound number, no card, handles
   turn-taking and barge-in), FastAPI and Pydantic (declarative validation of 19
   fields, free OpenAPI docs), Neon Postgres (survives redeploys where SQLite on
   a free tier does not), Render free tier with a keep-alive ping.
4. **Design decision: validation speaks** — validation lives in `schemas.py` and
   returns caller-readable text, so the agent re-prompts for one field without
   re-prompting rules in the prompt.
5. **Setup** — clone, `pip install -r requirements.txt`, copy `.env.example`,
   `uvicorn app.main:app --reload`, `pytest`.
6. **Environment variables** — `DATABASE_URL`, `VAPI_SECRET`, and for the push
   script `VAPI_API_KEY`, `VAPI_ASSISTANT_ID`, `BACKEND_URL`.
7. **API reference** — the five endpoints, the envelope, and a `curl` example
   for each.
8. **Prompt engineering** — point at `prompts/system_prompt.md` and
   `vapi/assistant.json`, and explain that the push script keeps them
   authoritative over the dashboard.
9. **Trade-offs and known limitations** — one endpoint instead of two for Vapi
   webhooks and why; `create_all` instead of Alembic; no auth on the API because
   the brief asks for a publicly testable endpoint; free-tier cold start
   mitigated by a ping rather than a paid instance; Vapi free numbers are
   US-domestic inbound only.
10. **Next steps** — Spanish support, appointment scheduling, API
    authentication, Alembic migrations, structured call analytics.

- [ ] **Step 7: Final verification and commit**

Run: `pytest -v`
Expected: PASS, all tests

```bash
git add render.yaml README.md
git commit -m "docs: add deployment configuration and README"
git push
```

---

## Self-Review

**Spec coverage:** Telephony and voice agent → Tasks 7, 10. Data model → Tasks 3, 4. Persistence → Tasks 3, 5, 11. REST API → Task 6. Voice-to-database integration → Task 7. Duplicate detection → Task 7 (`_handle_lookup`, `find_recent_duplicate`). Transcripts → Task 8. Dashboard → Task 9. Deployment, README, security, observability → Task 11 plus logging added in Tasks 6 and 7. Edge cases → Task 7 (invalid input, write failure, retry), Task 8 (dropped call), Task 10 (start over). Tests → every task.

**Type consistency:** `normalize_patient_payload` returns `date_of_birth` as an ISO string, which `PatientCreate` parses to `date` — checked. `spoken_error_for` takes a `pydantic.ValidationError`, which is what both `_handle_register` and `_handle_update` catch — checked. `find_recent_duplicate` takes a `date`, and `patient_data.date_of_birth` is a `date` after validation — checked. Tool names match between `HANDLERS`, `vapi/assistant.json`, and `prompts/system_prompt.md` — checked.

**Known gap accepted:** `_handle_end_of_call` is defined as a stub in Task 7 and replaced in Task 8 so Task 7's tests can pass independently. Task 8 replaces it outright.
