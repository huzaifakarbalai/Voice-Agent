"""All database access lives here. Both the REST router and the voice webhook
call these functions, so validation and soft-delete semantics cannot drift
between the phone path and the API path.

Nothing in this module imports from FastAPI. It takes plain Python in and
returns ORM objects out, which is what makes it testable without a web server.
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
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
        # Case-insensitive equality, not a wildcard match: the dashboard is
        # the only interactive search an assessor gets, and an exact,
        # case-sensitive comparison means a lowercase "doe" finds nothing for
        # "Doe". func.lower() behaves identically on SQLite and Postgres.
        statement = statement.where(func.lower(Patient.last_name) == last_name.lower())
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
    registration, not a second one.

    `cutoff` is timezone-aware UTC and is compared directly against
    `Patient.created_at`. That column is `UTCDateTime` (see
    app/models.py), which guarantees `created_at` is always aware UTC
    when read back -- on Postgres natively, and on SQLite by stamping
    the tzinfo SQLite itself drops. Without that type, this exact
    comparison is dialect-dependent: SQLite's plain `DateTime` returns
    naive datetimes, so filtering here would either raise
    `TypeError: can't compare offset-naive and offset-aware datetimes`
    (if compared in Python) or silently rely on both sides formatting to
    the same string (if compared, as here, inside a SQL `WHERE` clause) --
    correct only by luck, and only for as long as every value stays UTC.
    """
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
