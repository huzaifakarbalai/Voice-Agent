from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

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


def test_sex_check_constraint_rejects_invalid_values(db):
    """Verify that the CHECK constraint enforces only permitted sex values."""
    invalid_patient = Patient(
        first_name="John",
        last_name="Doe",
        date_of_birth=date(1990, 1, 1),
        sex="Unknown",
        phone_number="4155550142",
        address_line_1="1 Market St",
        city="San Francisco",
        state="CA",
        zip_code="94105",
    )
    db.add(invalid_patient)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_sex_accepts_all_permitted_values(db):
    """Verify that all four permitted sex values can be inserted."""
    permitted_values = ["Male", "Female", "Other", "Decline to Answer"]

    for sex_value in permitted_values:
        patient = Patient(
            first_name="Test",
            last_name="Patient",
            date_of_birth=date(1985, 6, 15),
            sex=sex_value,
            phone_number="4155550142",
            address_line_1="1 Market St",
            city="San Francisco",
            state="CA",
            zip_code="94105",
        )
        db.add(patient)
        db.commit()
        assert patient.sex == sex_value
