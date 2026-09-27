from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError, StatementError

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


# The tests below pin the behaviour of `UTCDateTime` (app/models.py). That
# type is a load-bearing, application-wide behaviour change -- it coerces
# every timestamp read to aware UTC and can raise on write -- and the whole
# cross-dialect correctness of `find_recent_duplicate` in
# app/services/patients.py rests on it. Without these tests, someone could
# "simplify" `UTCDateTime` back to a plain `DateTime(timezone=True)`, or
# introduce a typo in `process_result_value`, and nothing here would catch
# it. Do not delete these as redundant with test_patient_gets_uuid_and_timestamps_on_insert
# above -- that test only checks the in-memory value set at flush time, not
# a genuine round-trip through the database.


def _make_patient() -> Patient:
    return Patient(
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


def test_patient_timestamp_is_aware_utc_after_genuine_reload(db):
    patient = _make_patient()
    db.add(patient)
    db.commit()

    # Force a real round-trip through the database rather than reading the
    # in-memory value that was set at flush time.
    db.expire(patient)

    assert patient.created_at.tzinfo is not None
    assert patient.created_at.utcoffset() == timedelta(0)


def test_transcript_timestamp_is_aware_utc_after_genuine_reload(db):
    transcript = CallTranscript(call_id="call-1", transcript="hello", summary="greeting")
    db.add(transcript)
    db.commit()

    db.expire(transcript)

    assert transcript.created_at.tzinfo is not None
    assert transcript.created_at.utcoffset() == timedelta(0)


def test_naive_datetime_write_is_rejected(db):
    patient = _make_patient()
    db.add(patient)
    db.commit()

    patient.deleted_at = datetime(2020, 1, 1, 12, 0, 0)  # naive, no tzinfo
    with pytest.raises(StatementError) as exc_info:
        db.commit()
    # Pin what actually propagates: SQLAlchemy wraps the ValueError raised by
    # UTCDateTime.process_bind_param in a StatementError at flush time.
    assert isinstance(exc_info.value.orig, ValueError)
    db.rollback()


def test_aware_non_utc_datetime_is_normalized_to_utc_on_reload(db):
    patient = _make_patient()
    db.add(patient)
    db.commit()

    plus_five = timezone(timedelta(hours=5))
    local_time = datetime(2024, 6, 1, 17, 0, 0, tzinfo=plus_five)
    patient.deleted_at = local_time
    db.commit()
    db.expire(patient)

    assert patient.deleted_at.tzinfo is not None
    assert patient.deleted_at.utcoffset() == timedelta(0)
    # Same absolute instant, just re-expressed in UTC.
    assert patient.deleted_at == local_time
