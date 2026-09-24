import uuid
from datetime import date, datetime, timezone

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.db import Base


def _uuid4_str() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """`DateTime(timezone=True)` that always round-trips as aware UTC,
    on every dialect.

    Why this exists: Postgres (production) has a real `timestamptz` type
    and hands back aware UTC datetimes. SQLite (the whole test suite)
    has no timezone-aware storage at all -- the exact same
    `DateTime(timezone=True)` column silently comes back NAIVE there,
    even though every value this app ever writes is UTC. Code that reads
    `patient.created_at` (or `deleted_at`, `updated_at`) and compares it
    against an aware `datetime.now(timezone.utc)` would then get
    `TypeError: can't compare offset-naive and offset-aware datetimes`
    on SQLite while working fine on Postgres -- or, if the comparison is
    instead built as a SQLAlchemy expression (`Column >= value`) rather
    than a Python-level comparison, it can silently rely on both sides
    happening to format to the same UTC wall-clock string, which is true
    only as long as nobody ever binds a non-UTC value. Neither behavior
    should be left to chance.

    This type collapses both dialects to one observable behavior:
    - Read (`process_result_value`): a naive result (SQLite) is stamped
      with `tzinfo=UTC` -- safe, because every value this app writes
      already IS UTC, never local time. An aware result (Postgres) is
      normalized to UTC too, so a differently-configured session
      timezone can never change what Python sees.
    - Write (`process_bind_param`): naive input is rejected outright
      rather than silently assumed to be UTC. This forces every caller
      to be explicit (`datetime.now(timezone.utc)`), which in turn means
      SQLite's dialect never strips a meaningful offset -- the wall-clock
      fields it stores are always genuinely UTC -- and Postgres never
      receives a naive value whose interpretation would depend on the
      session's `TimeZone` setting.

    Net effect: application code can compare `patient.created_at`
    against an aware UTC datetime, or build a `Column >= value` filter
    with one, and get the same, correct answer on both dialects.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "naive datetime written to a UTCDateTime column; "
                "use datetime.now(timezone.utc), never a naive datetime"
            )
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # SQLite: every value we ever wrote was already UTC, so a
            # naive result is unambiguous -- just re-attach the offset.
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


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
    preferred_language: Mapped[str] = mapped_column(
        String(50), nullable=False, default="English", server_default="English"
    )
    emergency_contact_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(10), nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=_utcnow, onupdate=_utcnow
    )
    # Soft delete. Rows with a value here are invisible to list and get.
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (
        Index("ix_patients_phone_number", "phone_number"),
        Index("ix_patients_last_name", "last_name"),
        # CHECK constraint ensures sex is one of the permitted values; keeps the column
        # a plain string so Pydantic passes str, and avoids native ENUM type complications.
        CheckConstraint("sex IN ('Male', 'Female', 'Other', 'Decline to Answer')"),
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
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
