"""
Age bucketer — resolves age data from order attendee answers.

Looks for a custom question whose label contains "birth" (case-insensitive)
across all positions in the order.  Converts the found date to an age bucket.
The raw birthdate is NEVER stored.

Question label match: "Birth Date", "Birthdate", "Date of Birth", etc.
"""
from datetime import date, datetime
from typing import Optional


# Question label keywords used to identify the birth date question
_BIRTH_KEYWORDS = ("birth", "geboren", "nascita", "nacimiento")  # DE/IT/ES fallbacks

# Ordered buckets: (max_age_exclusive, label)
_BUCKETS = [
    (18, "0-17"),
    (25, "18-24"),
    (35, "25-34"),
    (45, "35-44"),
    (55, "45-54"),
    (65, "55-64"),
    (None, "65+"),
]


def _age_to_bucket(age: int) -> str:
    for max_age, label in _BUCKETS:
        if max_age is None or age < max_age:
            return label
    return "65+"


def _parse_birthdate(raw: str) -> Optional[date]:
    """Try common date formats."""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _is_birth_question(question_text: str) -> bool:
    lower = question_text.lower()
    return any(kw in lower for kw in _BIRTH_KEYWORDS)


def resolve_age_range(order) -> str:
    """
    Scan all positions/answers for a birth date question and return the age bucket.

    :param order: Pretix Order instance (positions/answers must be prefetched).
    :returns: Age bucket string or '' if not found / not parseable.
    """
    today = date.today()

    for position in order.positions.all():
        for answer in position.answers.all():
            question_text = str(answer.question.question)
            if not _is_birth_question(question_text):
                continue
            if not answer.answer:
                continue

            birthdate = _parse_birthdate(answer.answer)
            if birthdate is None:
                continue

            # Calculate age correctly accounting for birthday not yet passed
            age = (
                today.year
                - birthdate.year
                - ((today.month, today.day) < (birthdate.month, birthdate.day))
            )
            return _age_to_bucket(age)

    return ""


def resolve_age_confirmed(order) -> Optional[bool]:
    """
    Check if the buyer explicitly confirmed they are 18+ years old.

    Looks for a question containing "18" or "legal guardian".
    Returns True if answered affirmatively, False if not, None if question absent.
    """
    _CONFIRM_KEYWORDS = ("18", "legal guardian", "volljährig", "maggiorenne")

    for position in order.positions.all():
        for answer in position.answers.all():
            question_text = str(answer.question.question).lower()
            if any(kw in question_text for kw in _CONFIRM_KEYWORDS):
                ans = answer.answer.lower().strip()
                # Pretix stores checkbox answers as 'True' / 'False'
                if ans in ("true", "yes", "1", "ja", "si", "sì", "oui"):
                    return True
                if ans in ("false", "no", "0"):
                    return False

    return None
