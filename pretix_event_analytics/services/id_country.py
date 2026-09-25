"""
Document country from an ID-document number (opt-in per event).

The number is read in memory during ingestion and never stored — only the
resulting country code is kept, with source "id_document". A country is only
assigned when the number matches a national format *and* passes that
format's check digit, or when the format is unique to one country. Ambiguous
formats (most passports) yield nothing rather than a guess.

Note: an ID document shows the issuing country (usually nationality), not
residence. It is therefore used as a fallback after the sources that
describe residence.
"""
import re
from typing import Callable, List, Optional, Tuple

_ES_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def _clean(value: str) -> str:
    return re.sub(r"[\s\-.]", "", (value or "").upper())


def _es_dni(v: str) -> bool:
    m = re.fullmatch(r"(\d{8})([A-Z])", v)
    return bool(m) and _ES_LETTERS[int(m.group(1)) % 23] == m.group(2)


def _es_nie(v: str) -> bool:
    m = re.fullmatch(r"([XYZ])(\d{7})([A-Z])", v)
    if not m:
        return False
    num = int(str("XYZ".index(m.group(1))) + m.group(2))
    return _ES_LETTERS[num % 23] == m.group(3)


def _pt_civil_check(digits9: str) -> bool:
    """Portuguese civil identification number + check digit (mod 11)."""
    if not re.fullmatch(r"\d{9}", digits9):
        return False
    total = sum(int(d) * w for d, w in zip(digits9[:8], range(9, 1, -1)))
    check = 11 - total % 11
    check = 0 if check >= 10 else check
    return check == int(digits9[8])


def _pt_cc_document(v: str) -> bool:
    """Cartão de Cidadão document number: 8 digits, check digit, 2 letters/digits, check digit (mod 10)."""
    m = re.fullmatch(r"(\d{8})(\d)([A-Z0-9]{2})(\d)", v)
    if not m:
        return False
    def val(c):
        return int(c) if c.isdigit() else ord(c) - 55  # A=10 … Z=35
    chars = v
    total = 0
    for i, c in enumerate(reversed(chars)):
        n = val(c)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _il_teudat_zehut(v: str) -> bool:
    if not re.fullmatch(r"\d{5,9}", v):
        return False
    v = v.zfill(9)
    total = 0
    for i, d in enumerate(v):
        n = int(d) * (1 + i % 2)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def _be_national_number(v: str) -> bool:
    if not re.fullmatch(r"\d{11}", v):
        return False
    base, check = int(v[:9]), int(v[9:])
    return 97 - base % 97 == check or 97 - int("2" + v[:9]) % 97 == check


def _be_eid_card(v: str) -> bool:
    if not re.fullmatch(r"\d{12}", v):
        return False
    return int(v[:10]) % 97 == int(v[10:]) or (int(v[10:]) == 97 and int(v[:10]) % 97 == 0)


def _it_codice_fiscale(v: str) -> bool:
    if not re.fullmatch(r"[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]", v):
        return False
    odd = {**{str(i): x for i, x in enumerate([1, 0, 5, 7, 9, 13, 15, 17, 19, 21])},
           **dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                      [1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 2, 4, 18, 20, 11, 3, 6, 8, 12, 14, 16, 10, 22, 25, 24, 23]))}
    even = {**{str(i): i for i in range(10)}, **{c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")}}
    total = sum(odd[c] if i % 2 == 0 else even[c] for i, c in enumerate(v[:15]))
    return chr(65 + total % 26) == v[15]


# (rule id, country, validator on the cleaned value)
RULES: List[Tuple[str, str, Callable[[str], bool]]] = [
    ("es_dni", "ES", _es_dni),
    ("es_nie", "ES", _es_nie),
    ("pt_cc_document", "PT", _pt_cc_document),
    ("it_codice_fiscale", "IT", _it_codice_fiscale),
    ("be_eid_card", "BE", _be_eid_card),
    ("be_national_number", "BE", _be_national_number),
    ("fr_passport", "FR", lambda v: bool(re.fullmatch(r"\d{2}[A-Z]{2}\d{5}", v))),
    ("gb_driving_licence", "GB", lambda v: bool(re.fullmatch(r"[A-Z9]{5}\d{6}[A-Z9]{2}\d[A-Z]{2}(\d{2})?", v))),
    # 9 digits: Portuguese civil number + check digit, or Israeli ID. When both
    # checks pass the number is ambiguous and yields nothing.
    ("pt_civil_9", "PT", lambda v: _pt_civil_check(v) and not _il_teudat_zehut(v)),
    ("il_teudat_zehut", "IL", lambda v: len(v) in (8, 9) and _il_teudat_zehut(v) and not _pt_civil_check(v.zfill(9))),
    # Plain 8-digit Portuguese civil number: no check digit. Only enabled if
    # measurement on real data shows it is reliable (see UNVERIFIED).
    ("pt_civil_8", "PT", lambda v: bool(re.fullmatch(r"\d{8}", v))),
]

# Rules without a check digit; used only when enabled after measurement.
UNVERIFIED = {"pt_civil_8"}

# Measured 2026-09-25 against orders with a known invoice-address country
# (SUTI 2022–2026, 3,097 ID answers). Precision vs invoice country:
#   pt_civil_8 92% (195/212) · es_dni 90% · es_nie 6/6 · pt_cc_document 6/7 ·
#   gb_driving_licence 8/8 · be_* 2/2 (strong mod-97 check)
#   il_teudat_zehut 35% — many PT numbers pass its Luhn check by chance
#   fr_passport 58% — format shared with other passports
#   pt_civil_9 5/9 — too few and too noisy
DISABLED: set = {"il_teudat_zehut", "fr_passport", "pt_civil_9"}
ENABLED_UNVERIFIED: set = {"pt_civil_8"}


def document_country(raw: str) -> Optional[str]:
    """Country code from an ID-document number, or None when not confident."""
    v = _clean(raw)
    if len(v) < 6:
        return None
    hits = {country for rule, country, check in RULES
            if rule not in DISABLED and (rule not in UNVERIFIED or rule in ENABLED_UNVERIFIED) and check(v)}
    return hits.pop() if len(hits) == 1 else None


def matching_rule(raw: str) -> Optional[str]:
    """Rule id that matched (for measuring precision), or None."""
    v = _clean(raw)
    for rule, _country, check in RULES:
        if check(v):
            return rule
    return None
