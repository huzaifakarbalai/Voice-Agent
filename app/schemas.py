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
