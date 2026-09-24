from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.models import Patient
from app.schemas import PatientCreate, PatientOut, PatientUpdate, spoken_error_for

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


def test_lowercase_sex_is_rejected_with_spoken_error():
    with pytest.raises(ValidationError) as exc:
        PatientCreate(**{**VALID, "sex": "male"})
    assert "accepted options" in spoken_error_for(exc.value).lower()


def test_malformed_email_is_rejected_with_spoken_error():
    with pytest.raises(ValidationError) as exc:
        PatientCreate(**{**VALID, "email": "not-an-email"})
    assert "email" in spoken_error_for(exc.value).lower()


def test_short_emergency_contact_phone_is_rejected_with_spoken_error():
    with pytest.raises(ValidationError) as exc:
        PatientCreate(**{**VALID, "emergency_contact_phone": "555"})
    assert "emergency contact phone" in spoken_error_for(exc.value).lower()


def test_malformed_insurance_member_id_is_rejected_with_spoken_error():
    with pytest.raises(ValidationError) as exc:
        PatientCreate(**{**VALID, "insurance_member_id": "-XYZ123"})
    assert "insurance member id" in spoken_error_for(exc.value).lower()


def test_insurance_member_id_allows_hyphens_and_spaces():
    patient = PatientCreate(**{**VALID, "insurance_member_id": "XYZ-123 456"})
    assert patient.insurance_member_id == "XYZ-123 456"


@pytest.mark.parametrize("field", ["state", "sex", "zip_code", "phone_number", "first_name"])
def test_update_rejects_explicit_blank_on_not_null_field(field):
    with pytest.raises(ValidationError):
        PatientUpdate(**{field: ""})


@pytest.mark.parametrize("field", ["state", "sex", "zip_code", "phone_number", "first_name"])
def test_update_rejects_explicit_null_on_not_null_field(field):
    with pytest.raises(ValidationError):
        PatientUpdate(**{field: None})


def test_update_blank_state_gives_the_state_spoken_error():
    with pytest.raises(ValidationError) as exc:
        PatientUpdate(state="")
    assert "state" in spoken_error_for(exc.value).lower()


def test_update_blank_sex_gives_the_sex_spoken_error():
    with pytest.raises(ValidationError) as exc:
        PatientUpdate(sex="")
    assert "accepted options" in spoken_error_for(exc.value).lower()


def test_update_blank_email_clears_the_field_without_raising():
    update = PatientUpdate(email="")
    assert update.email is None


def test_update_blank_emergency_contact_phone_clears_the_field_without_raising():
    update = PatientUpdate(emergency_contact_phone="")
    assert update.emergency_contact_phone is None


def test_patient_out_round_trips_from_orm_row(db):
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

    out = PatientOut.model_validate(patient)

    assert out.patient_id == patient.patient_id
    assert isinstance(out.patient_id, str)
    assert out.first_name == "Jane"
    assert out.date_of_birth == date(1992, 1, 5)
    assert out.created_at == patient.created_at
    assert not hasattr(out, "deleted_at")
