"""Speech transcripts are not clean input. These functions run before validation
so the agent is never asked to re-prompt for a value the caller actually got right.

Every function returns None when it cannot confidently normalize. The caller
passes the original value through so the validation layer produces the error
message, keeping all rejection text in one place.
"""

import re
from datetime import date, datetime

from dateutil import parser as date_parser

STATE_ABBREVIATIONS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}

STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "washington d.c.": "DC",
}

DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
    "nine": "9",
}

ORDINAL_WORDS = {
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
    "sixth": "6", "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10",
    "eleventh": "11", "twelfth": "12", "thirteenth": "13", "fourteenth": "14",
    "fifteenth": "15", "sixteenth": "16", "seventeenth": "17",
    "eighteenth": "18", "nineteenth": "19", "twentieth": "20",
    "twenty-first": "21", "twenty-second": "22", "twenty-third": "23",
    "twenty-fourth": "24", "twenty-fifth": "25", "twenty-sixth": "26",
    "twenty-seventh": "27", "twenty-eighth": "28", "twenty-ninth": "29",
    "thirtieth": "30", "thirty-first": "31",
}

SEX_SYNONYMS = {
    "m": "Male", "male": "Male", "man": "Male",
    "f": "Female", "female": "Female", "woman": "Female",
    "other": "Other", "non-binary": "Other", "nonbinary": "Other",
    "decline to answer": "Decline to Answer", "decline": "Decline to Answer",
    "prefer not to say": "Decline to Answer", "prefer not to answer": "Decline to Answer",
    "no answer": "Decline to Answer", "skip": "Decline to Answer",
}

ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")

# --- Spoken year expansion --------------------------------------------------
#
# dateutil parses "January 5th, 1992" fine but has no idea what to do with
# "nineteen ninety-two" -- a birth year an American caller says just as
# naturally as a numeral. This expands the small, closed set of ways people
# actually say a year out loud. It is deliberately NOT a general English
# number parser: only these specific shapes are recognized. Anything else
# ("nineteen hundred and five", or any other phrasing) is left as words,
# which dateutil then fails to parse, so normalize_date returns None rather
# than silently guessing a year -- a wrong date of birth silently written to
# a patient record is worse than one extra re-prompt.

_YEAR_ONES = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9,
}
_YEAR_ZERO = {"oh": 0, "o": 0, "zero": 0}
_YEAR_TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_YEAR_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

_YEAR_SEP = r"[\s-]+"


def _alt(words) -> str:
    return "(?:" + "|".join(words) + ")"


_ONES_ALT = _alt(_YEAR_ONES)
_ZERO_ALT = _alt(_YEAR_ZERO)
_TENS_ALT = _alt(_YEAR_TENS)
_TEENS_ALT = _alt(_YEAR_TEENS)


def _year_1900_zero_ones(m: re.Match) -> str:
    # "nineteen oh five" -> 1905
    return str(1900 + _YEAR_ONES[m.group(2)])


def _year_1900_tens_ones(m: re.Match) -> str:
    # "nineteen eighty" -> 1980, "nineteen ninety-two" -> 1992
    year = 1900 + _YEAR_TENS[m.group(1)]
    if m.group(2):
        year += _YEAR_ONES[m.group(2)]
    return str(year)


def _year_2000_thousand(m: re.Match) -> str:
    # "two thousand" -> 2000, "two thousand one" / "two thousand and one" -> 2001
    year = 2000
    if m.group(1):
        year += _YEAR_ONES[m.group(1)]
    return str(year)


def _year_2000_teens(m: re.Match) -> str:
    # "twenty ten" -> 2010
    return str(2000 + _YEAR_TEENS[m.group(1)])


def _year_2000_tens_ones(m: re.Match) -> str:
    # "twenty twenty" -> 2020, "twenty twenty one" -> 2021
    year = 2000 + _YEAR_TENS[m.group(1)]
    if m.group(2):
        year += _YEAR_ONES[m.group(2)]
    return str(year)


def _year_2000_zero_ones(m: re.Match) -> str:
    # "twenty oh five" -> 2005
    return str(2000 + _YEAR_ONES[m.group(2)])


# Checked in order; each pattern only fires on one fixed, known spoken-year
# shape. A phrase that matches none of them (e.g. "nineteen hundred and
# five") is returned unchanged by _expand_spoken_years below.
_YEAR_PATTERNS = [
    (re.compile(rf"\bnineteen{_YEAR_SEP}({_ZERO_ALT}){_YEAR_SEP}({_ONES_ALT})\b"), _year_1900_zero_ones),
    (re.compile(rf"\bnineteen{_YEAR_SEP}({_TENS_ALT})(?:{_YEAR_SEP}({_ONES_ALT}))?\b"), _year_1900_tens_ones),
    (re.compile(rf"\btwo{_YEAR_SEP}thousand(?:{_YEAR_SEP}and)?(?:{_YEAR_SEP}({_ONES_ALT}))?\b"), _year_2000_thousand),
    (re.compile(rf"\btwenty{_YEAR_SEP}({_TEENS_ALT})\b"), _year_2000_teens),
    (re.compile(rf"\btwenty{_YEAR_SEP}({_TENS_ALT})(?:{_YEAR_SEP}({_ONES_ALT}))?\b"), _year_2000_tens_ones),
    (re.compile(rf"\btwenty{_YEAR_SEP}({_ZERO_ALT}){_YEAR_SEP}({_ONES_ALT})\b"), _year_2000_zero_ones),
]


def _expand_spoken_years(text: str) -> str:
    """Replace one recognized spoken year (see _YEAR_PATTERNS) with digits.
    Text that matches none of the known shapes is returned unchanged, so the
    dateutil parse that follows fails and normalize_date returns None rather
    than guessing."""
    for pattern, handler in _YEAR_PATTERNS:
        match = pattern.search(text)
        if match:
            return text[: match.start()] + handler(match) + text[match.end():]
    return text


def _words_to_digits(raw: str) -> str:
    """Replace spelled-out digits with numerals, leaving everything else alone."""
    tokens = re.split(r"[\s\-,]+", raw.strip().lower())
    return "".join(DIGIT_WORDS.get(token, token) for token in tokens)


def normalize_phone(raw) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", _words_to_digits(str(raw)))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def normalize_state(raw) -> str | None:
    if not raw:
        return None
    text = str(raw).strip()
    if text.upper() in STATE_ABBREVIATIONS:
        return text.upper()
    return STATE_NAMES.get(re.sub(r"\s+", " ", text.lower()))


def normalize_zip(raw) -> str | None:
    if not raw:
        return None
    text = str(raw).strip()
    if ZIP_RE.match(text):
        return text
    digits = re.sub(r"\D", "", _words_to_digits(text))
    if len(digits) == 5:
        return digits
    if len(digits) == 9:
        return f"{digits[:5]}-{digits[5:]}"
    return None


def normalize_date(raw) -> date | None:
    if not raw:
        return None
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    # Spoken forms: "January fifth 1992", "January 5th, 1992",
    # "January fifth nineteen ninety-two".
    lowered = text.lower()
    for word, numeral in ORDINAL_WORDS.items():
        lowered = re.sub(rf"\b{re.escape(word)}\b", numeral, lowered)
    lowered = re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1", lowered)
    lowered = _expand_spoken_years(lowered)
    try:
        # dateutil silently invents any component (year, month, or day) the
        # text does not actually specify, by filling it in from `default`.
        # A caller who answers "what is your date of birth" with just a year
        # is a normal partial answer on a phone call, and a fabricated day
        # and month -- today's, whatever today happens to be -- would be a
        # real, plausible, completely wrong date of birth silently written to
        # the record; nothing downstream can detect it after the fact. So the
        # text is parsed twice against two different defaults: a component
        # the text actually supplied comes out identical both times, and any
        # component dateutil had to invent differs between the two parses.
        # If anything differs, the input was incomplete and this returns
        # None -- a re-prompt -- rather than guessing.
        first = date_parser.parse(lowered, dayfirst=False, default=datetime(2000, 1, 1))
        second = date_parser.parse(lowered, dayfirst=False, default=datetime(2001, 2, 2))
        if (first.year, first.month, first.day) != (second.year, second.month, second.day):
            return None
        return first.date()
    except (ValueError, OverflowError, TypeError):
        return None


def normalize_sex(raw) -> str | None:
    if not raw:
        return None
    return SEX_SYNONYMS.get(re.sub(r"\s+", " ", str(raw).strip().lower()))


def normalize_patient_payload(payload: dict) -> dict:
    """Normalize the fields we know how to normalize. Anything that fails to
    normalize is passed through unchanged so the schema layer rejects it with
    the right message."""
    result = dict(payload)

    for key in (
        "first_name", "last_name", "address_line_1", "address_line_2", "city",
        "insurance_provider", "insurance_member_id", "preferred_language",
        "emergency_contact_name", "email",
    ):
        if isinstance(result.get(key), str):
            result[key] = result[key].strip()

    for key in ("phone_number", "emergency_contact_phone"):
        if result.get(key) is not None:
            result[key] = normalize_phone(result[key]) or result[key]

    if result.get("state") is not None:
        result["state"] = normalize_state(result["state"]) or result["state"]

    if result.get("zip_code") is not None:
        result["zip_code"] = normalize_zip(result["zip_code"]) or result["zip_code"]

    if result.get("sex") is not None:
        result["sex"] = normalize_sex(result["sex"]) or result["sex"]

    if result.get("date_of_birth") is not None:
        parsed = normalize_date(result["date_of_birth"])
        result["date_of_birth"] = parsed.isoformat() if parsed else result["date_of_birth"]

    return result
