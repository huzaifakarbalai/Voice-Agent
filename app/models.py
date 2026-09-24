import uuid
from datetime import date, datetime, timezone

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, String, Text
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
    preferred_language: Mapped[str] = mapped_column(
        String(50), nullable=False, default="English", server_default="English"
    )
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
