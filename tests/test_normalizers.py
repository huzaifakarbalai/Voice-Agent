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


def test_date_accepts_numeric_year_regression():
    # Same phrase as the spoken-year cases below, but with a plain numeral —
    # this must keep working exactly as it did before spoken-year expansion
    # was added.
    assert normalize_date("January fifth 1992") == date(1992, 1, 5)


def test_date_expands_spoken_years():
    assert normalize_date("January fifth nineteen ninety-two") == date(1992, 1, 5)
    assert normalize_date("March third two thousand one") == date(2001, 3, 3)
    assert normalize_date("June twelfth nineteen seventy five") == date(1975, 6, 12)
    assert normalize_date("April ninth nineteen eighty") == date(1980, 4, 9)
    assert normalize_date("May second nineteen oh five") == date(1905, 5, 2)
    assert normalize_date("July fourth two thousand") == date(2000, 7, 4)
    assert normalize_date("August sixth two thousand and one") == date(2001, 8, 6)
    assert normalize_date("September first twenty ten") == date(2010, 9, 1)
    assert normalize_date("October second twenty twenty") == date(2020, 10, 2)
    assert normalize_date("November third twenty oh five") == date(2005, 11, 3)


def test_date_unknown_spoken_year_form_returns_none_rather_than_guessing():
    # "nineteen hundred and five" is not one of the recognized shapes.
    # normalize_date must not guess a year here -- it must return None so
    # the caller is re-prompted instead of a wrong DOB being saved.
    assert normalize_date("January fifth nineteen hundred and five") is None


def test_date_unparseable_phrase_returns_none():
    assert normalize_date("January fifth banana") is None


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
