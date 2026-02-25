"""
Van length bucketer — detects caravan pass products and extracts van length.

A caravan pass product is identified by the presence of a "camper"/"caravan"
question on any order position.  Van length answers are bucketed for venue
capacity planning.  License plates are deliberately ignored (PII).

Bucket definitions:
  '<6m'  — compact, e.g. small camper vans
  '6-8m' — mid-size, most caravan vans
  '>8m'  — large, requires extended pitch
  ''     — not provided / not applicable
"""
import re
from typing import Tuple

# Keywords that identify a van length question
_LENGTH_KEYWORDS = ("length", "lang", "lunghezza", "long", "meter", "metre", "m ")
# Keywords that identify any caravan-related question (not just length)
_CARAVAN_KEYWORDS = ("camper", "caravan", "wohnmobil", "campervan", "motorhome")


def _is_caravan_question(question_text: str) -> bool:
    lower = question_text.lower()
    return any(kw in lower for kw in _CARAVAN_KEYWORDS)


def _is_length_question(question_text: str) -> bool:
    lower = question_text.lower()
    return any(kw in lower for kw in _LENGTH_KEYWORDS)


def _parse_length_meters(raw: str) -> float:
    """Extract numeric value from a length answer, assuming meters."""
    # Strip units and extract first number (handles "7m", "7.5 meters", "750 cm")
    raw = raw.lower().strip()

    # Handle centimetre inputs: e.g. "750", "750cm"
    cm_match = re.search(r"(\d+(?:\.\d+)?)\s*cm", raw)
    if cm_match:
        return float(cm_match.group(1)) / 100.0

    # Handle bare number that looks like centimetres (> 20 → likely cm)
    num_match = re.search(r"(\d+(?:\.\d+)?)", raw)
    if num_match:
        value = float(num_match.group(1))
        if value > 20:
            return value / 100.0
        return value

    raise ValueError(f"Cannot parse length: {raw!r}")


def _length_to_bucket(meters: float) -> str:
    if meters < 6.0:
        return "<6m"
    elif meters <= 8.0:
        return "6-8m"
    else:
        return ">8m"


def resolve_caravan_data(order) -> Tuple[bool, str]:
    """
    Scan order positions for caravan pass signals.

    :param order: Pretix Order instance (positions/answers must be prefetched).
    :returns: Tuple of (has_caravan_pass: bool, camper_van_length_bucket: str).
    """
    has_caravan = False
    length_bucket = ""

    for position in order.positions.all():
        for answer in position.answers.all():
            question_text = str(answer.question.question)

            if not _is_caravan_question(question_text):
                continue

            has_caravan = True

            # If this is also a length question and we haven't bucketed yet
            if not length_bucket and _is_length_question(question_text) and answer.answer:
                try:
                    meters = _parse_length_meters(answer.answer)
                    length_bucket = _length_to_bucket(meters)
                except (ValueError, TypeError):
                    pass

    return has_caravan, length_bucket


def resolve_caravan_data_for_position(position) -> Tuple[bool, str]:
    """
    Same as resolve_caravan_data but for a single OrderPosition.
    Used when building AnalyticsTicketFact rows.
    """
    has_caravan = False
    length_bucket = ""

    for answer in position.answers.all():
        question_text = str(answer.question.question)
        if not _is_caravan_question(question_text):
            continue
        has_caravan = True
        if not length_bucket and _is_length_question(question_text) and answer.answer:
            try:
                meters = _parse_length_meters(answer.answer)
                length_bucket = _length_to_bucket(meters)
            except (ValueError, TypeError):
                pass

    return has_caravan, length_bucket
