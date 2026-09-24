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
