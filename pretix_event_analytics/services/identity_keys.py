"""
Person-matching keys built from a ticket holder's name and birth date.

Names are normalised before hashing so that "João  Silva", "joao silva" and
"Silva, João" produce the same key: accents stripped, lowercased,
punctuation dropped, name particles ("da", "de", "van"…) and single-letter
initials ignored. Only HMAC hashes leave this module.

Keys (``identity_type`` values)
-------------------------------
Certain — each includes the birth date, so different birth dates never match:

``nm_dob``   every name word (order ignored) + birth date
``fl_dob``   first + last name + birth date (middle names ignored)
             — also emitted for first + second-to-last name, so "Rui Alves"
             meets "Rui Alves Pereira" (Iberian double surnames)

Name only — used as *candidates* for probable matches, never to link alone:

``nm``       every name word (order ignored)
``fl``       first + last name

Names with fewer than two words produce no key: "Ana" + a birth date is not
specific enough to call someone the same person.
"""
import re
import unicodedata
from datetime import date
from typing import Dict, List, Optional

from .hash_service import generate_repeat_hash

CERTAIN_TYPES = ("nm_dob", "fl_dob")
NAME_TYPES = ("nm", "fl")
PERSON_TYPES = CERTAIN_TYPES + NAME_TYPES

PARTICLES = frozenset({
    "da", "das", "de", "del", "della", "der", "des", "di", "do", "dos", "du", "e", "el",
    "la", "le", "van", "von", "y", "zu",
})


def name_tokens(name: str) -> List[str]:
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    words = re.sub(r"[^a-z]+", " ", text).split()
    return [w for w in words if len(w) > 1 and w not in PARTICLES]


def plausible_birthdate(bd: Optional[date], ref: Optional[date]) -> bool:
    """Reject placeholder or mistyped dates (future, under 5, over 100 years old)."""
    if not bd:
        return False
    ref = ref or date.today()
    years = (ref - bd).days / 365.25
    return 5 <= years <= 100


def person_keys(name: str, birthdate: Optional[date], ref: Optional[date] = None) -> List[Dict]:
    """Identity dicts ({type, hash}) for one ticket holder."""
    tokens = name_tokens(name)
    if len(tokens) < 2:
        return []
    full = " ".join(sorted(tokens))
    fl = f"{tokens[0]} {tokens[-1]}"
    raw = [("nm", full), ("fl", fl)]
    if plausible_birthdate(birthdate, ref):
        d = birthdate.isoformat()
        raw += [("nm_dob", f"{full}|{d}"), ("fl_dob", f"{fl}|{d}")]
        if len(tokens) >= 3:
            raw.append(("fl_dob", f"{tokens[0]} {tokens[-2]}|{d}"))
    return [{"type": t, "hash": generate_repeat_hash(f"{t}:{v}")} for t, v in raw]
