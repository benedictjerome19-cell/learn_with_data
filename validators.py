"""
Input validation helpers shared by the manual log form, log editing, bulk import
and account management. Every function returns (clean_value, error_message).
"""

import math
import re
from datetime import date, datetime, timedelta

MAX_LANGUAGE_LEN = 40
MAX_TOPIC_LEN = 200
MAX_NOTES_LEN = 1000
MAX_CONCEPTS = 500
EARLIEST_DATE = date(2000, 1, 1)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value, max_len):
    """Strips control characters and surrounding whitespace, then truncates."""
    if value is None:
        return ""
    text = _CONTROL_CHARS_RE.sub("", str(value)).strip()
    return text[:max_len]


def latest_allowed_date():
    # One day of slack so users ahead of the server's timezone can log "today"
    return date.today() + timedelta(days=1)


def parse_date(value):
    """Accepts YYYY-MM-DD strings, date or datetime objects."""
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None, "Date is required."
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None, "Date must be in YYYY-MM-DD format."
    if parsed < EARLIEST_DATE:
        return None, f"Date cannot be earlier than {EARLIEST_DATE.isoformat()}."
    if parsed > latest_allowed_date():
        return None, "Date cannot be in the future."
    return parsed.isoformat(), None


def _parse_number(value, cast, label):
    if isinstance(value, bool) or value is None or (isinstance(value, str) and not value.strip()):
        return None, f"{label} is required."
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f"{label} must be a number."
    if not math.isfinite(number):
        return None, f"{label} must be a finite number."
    if cast is int:
        if number != int(number):
            return None, f"{label} must be a whole number."
        return int(number), None
    return number, None


def parse_concepts(value):
    number, err = _parse_number(value, int, "Concepts count")
    if err:
        return None, err
    if not 1 <= number <= MAX_CONCEPTS:
        return None, f"Concepts count must be between 1 and {MAX_CONCEPTS}."
    return number, None


def parse_hours(value):
    number, err = _parse_number(value, float, "Hours spent")
    if err:
        return None, err
    if not 0.1 <= number <= 24:
        return None, "Hours spent must be between 0.1 and 24."
    return round(number, 2), None


def parse_mastery(value):
    number, err = _parse_number(value, int, "Mastery score")
    if err:
        return None, err
    if not 1 <= number <= 5:
        return None, "Mastery score must be between 1 and 5."
    return number, None


def validate_log_payload(data):
    """
    Validates a manual/edited learning log. Returns (clean_dict, error).
    Missing optional fields fall back to the same defaults the form uses.
    """
    if not isinstance(data, dict):
        return None, "Request body must be a JSON object."

    date_val, err = parse_date(data.get("date") or date.today().isoformat())
    if err:
        return None, err

    language = clean_text(data.get("language") or "Python", MAX_LANGUAGE_LEN)
    if not language:
        return None, "Language / Stack is required."

    topic = clean_text(data.get("topic"), MAX_TOPIC_LEN + 1)
    if not topic:
        return None, "Topic / Concept Area is required."
    if len(topic) > MAX_TOPIC_LEN:
        return None, f"Topic must be {MAX_TOPIC_LEN} characters or fewer."

    concepts, err = parse_concepts(data.get("concepts_count", 1))
    if err:
        return None, err
    hours, err = parse_hours(data.get("hours_spent", 1.0))
    if err:
        return None, err
    mastery, err = parse_mastery(data.get("mastery_score", 3))
    if err:
        return None, err

    notes = clean_text(data.get("notes"), MAX_NOTES_LEN + 1)
    if len(notes) > MAX_NOTES_LEN:
        return None, f"Notes must be {MAX_NOTES_LEN} characters or fewer."

    return {
        "date": date_val,
        "language": language,
        "topic": topic,
        "concepts_count": concepts,
        "hours_spent": hours,
        "mastery_score": mastery,
        "notes": notes,
    }, None


def validate_username(username):
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        return None, "Username must be 3-30 characters: letters, numbers, dot, dash or underscore."
    return username, None


def validate_email(email):
    email = (email or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        return None, "Please enter a valid email address."
    return email, None


def validate_password(password):
    password = password or ""
    if len(password) < 8:
        return None, "Password must be at least 8 characters."
    if len(password) > 128:
        return None, "Password must be 128 characters or fewer."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return None, "Password must contain at least one letter and one number."
    return password, None
