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
# Insurance member IDs routinely contain hyphens and spaces (e.g. "XYZ-123456"),
# so this only requires an alphanumeric first character rather than a strict
# alphanumeric-only pattern — rejecting a caller's genuine ID mid-call is worse
# than the gap a looser pattern leaves.
INSURANCE_MEMBER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-]{0,49}$")
MAX_AGE_YEARS = 130
MAX_EMAIL_LENGTH = 254

SPOKEN_ERRORS: dict[str, str] = {
    "first_name": "I did not catch a usable first name. Please ask the caller to say and, if needed, spell their first name.",
    "last_name": "I did not catch a usable last name. Please ask the caller to say and, if needed, spell their last name.",
    "date_of_birth": "That date of birth is not valid. Please ask the caller for their date of birth again, including the year.",
    "sex": "That is not one of the accepted options. Please ask whether the caller would like to say Male, Female, Other, or decline to answer.",
    "phone_number": "That phone number is not a valid ten digit US number. Please ask the caller to repeat their phone number including the area code.",
    "email": "That email address is not valid. Please ask the caller to repeat it slowly, or offer to skip it since it is optional.",
    "address_line_1": "I did not catch a usable street address. Please ask the caller for their street number and street name.",
    "city": "I did not catch a usable city. Please ask the caller for their city.",
    "state": "That is not a valid US state. Please ask the caller for their state.",
    "zip_code": "That ZIP code is not valid. Please ask the caller for their five digit ZIP code.",
    "emergency_contact_phone": "That emergency contact phone number is not a valid ten digit US number. Please ask the caller to repeat it.",
    "insurance_member_id": "That insurance member ID does not look right. Please ask the caller to repeat it, or note that insurance information is optional and can be skipped.",
}

GENERIC_SPOKEN_ERROR = "Something about that information was not valid. Please ask the caller to repeat the last answer."

# Spoken-friendly names for the "missing required field" branch of
# spoken_error_for, so the agent says "street address" instead of reading the
# underscored field name aloud. Fields not listed fall back to
# field.replace("_", " ").
FIELD_SPOKEN_NAMES: dict[str, str] = {
    "first_name": "first name",
    "last_name": "last name",
    "date_of_birth": "date of birth",
    "phone_number": "phone number",
    "address_line_1": "street address",
    "address_line_2": "second address line",
    "zip_code": "ZIP code",
    "emergency_contact_name": "emergency contact name",
    "emergency_contact_phone": "emergency contact phone number",
    "insurance_provider": "insurance provider",
    "insurance_member_id": "insurance member ID",
    "preferred_language": "preferred language",
}

ShortText = Annotated[str, StringConstraints(min_length=1, max_length=100, strip_whitespace=True)]
AddressText = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]
BoundedEmail = Annotated[EmailStr, StringConstraints(max_length=MAX_EMAIL_LENGTH)]


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


def _validate_sex(value: str) -> str:
    if value not in SEX_VALUES:
        raise ValueError(f"must be one of {', '.join(SEX_VALUES)}")
    return value


def _validate_state(value: str) -> str:
    upper = value.strip().upper()
    if upper not in STATE_ABBREVIATIONS:
        raise ValueError("must be a valid 2-letter US state abbreviation")
    return upper


def _validate_zip(value: str) -> str:
    if not ZIP_RE.match(value.strip()):
        raise ValueError("must be a 5-digit or ZIP+4 US ZIP code")
    return value.strip()


def _validate_insurance_member_id(value: str) -> str:
    if not INSURANCE_MEMBER_ID_RE.match(value.strip()):
        raise ValueError(
            "must start with a letter or digit and contain only letters, digits, spaces, or hyphens"
        )
    return value.strip()


class PatientBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: BoundedEmail | None = None
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
        return _validate_sex(value)

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
        return _validate_state(value)

    @field_validator("zip_code")
    @classmethod
    def check_zip(cls, value: str) -> str:
        return _validate_zip(value)

    @field_validator("insurance_member_id")
    @classmethod
    def check_insurance_member_id(cls, value: str | None) -> str | None:
        return _validate_insurance_member_id(value) if value else None


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
    email: BoundedEmail | None = None
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
        return _validate_sex(value) if value else value

    @field_validator("phone_number", "emergency_contact_phone")
    @classmethod
    def check_phone(cls, value: str | None) -> str | None:
        return _validate_phone(value) if value else None

    @field_validator("state")
    @classmethod
    def check_state(cls, value: str | None) -> str | None:
        return _validate_state(value) if value else None

    @field_validator("zip_code")
    @classmethod
    def check_zip(cls, value: str | None) -> str | None:
        return _validate_zip(value) if value else None

    @field_validator("insurance_member_id")
    @classmethod
    def check_insurance_member_id(cls, value: str | None) -> str | None:
        return _validate_insurance_member_id(value) if value else None


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
        pretty = FIELD_SPOKEN_NAMES.get(field, field.replace("_", " "))
        return f"The {pretty} is required and was not provided. Please ask the caller for their {pretty}."
    return SPOKEN_ERRORS.get(field, GENERIC_SPOKEN_ERROR)
