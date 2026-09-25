"""
Legacy editions: past attendee lists that predate Pretix or this plugin.

The organiser uploads (or pastes) a list from a past edition:

* any file that contains e-mail addresses — a mailing-list export, an old
  ticketing CSV. Addresses count as past *customers* (order e-mail);
* a CSV with a header row naming a **name** column and a **birth date**
  column (e.g. ``Name,Birth date`` or ``First name,Last name,DOB``). Each row
  becomes a past *person*, matched to ticket holders exactly like Pretix
  tickets (name + birth date). E-mail columns on the same row are kept too.

Values are normalised and HMAC-hashed in memory; only the hashes are stored.
The uploaded content is never written anywhere.
"""
import csv
import io
import re
from datetime import date
from typing import Dict, List, Optional, Tuple

from django.db import transaction

from .hash_service import generate_repeat_hash

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-']+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

_FULL_NAME = ("name", "full name", "fullname", "attendee name", "attendee", "nome", "nome completo", "nombre")
_FIRST = ("first name", "firstname", "given name", "primeiro nome", "nome proprio")
_LAST = ("last name", "lastname", "surname", "family name", "apelido", "apellidos", "sobrenome")
_BIRTH = ("birth", "dob", "nascimento", "nacimiento", "geburt")


def extract_hashes(raw: str) -> set:
    return {generate_repeat_hash(m.strip().lower()) for m in EMAIL_RE.findall(raw or "")} - {""}


def _norm(header: str) -> str:
    import unicodedata

    text = "".join(c for c in unicodedata.normalize("NFKD", header or "") if not unicodedata.combining(c))
    return re.sub(r"[^a-z]+", " ", text.lower()).strip()


def _columns(header: List[str]) -> Optional[Dict[str, List[int]]]:
    """Column indexes for name / first / last / birth / email, or None if not a person list."""
    cols = {"name": [], "first": [], "last": [], "birth": [], "email": []}
    for i, h in enumerate(_norm(x) for x in header):
        if any(k in h for k in _BIRTH):
            cols["birth"].append(i)
        elif "mail" in h:
            cols["email"].append(i)
        elif h in _FIRST:
            cols["first"].append(i)
        elif h in _LAST:
            cols["last"].append(i)
        elif h in _FULL_NAME:
            cols["name"].append(i)
    has_name = cols["name"] or (cols["first"] and cols["last"])
    return cols if has_name and cols["birth"] else None


def parse_people(raw: str, edition_year: int) -> List[List[Dict]]:
    """Identity rows per person from a CSV with name + birth-date columns ([] if it is not one)."""
    from .age_bucketer import _parse_birthdate
    from .identity_keys import person_keys

    text = (raw or "").lstrip("﻿")
    first_line = text.split("\n", 1)[0]
    try:
        dialect = csv.Sniffer().sniff(first_line, delimiters=",;\t")
    except csv.Error:
        return []
    reader = csv.reader(io.StringIO(text), dialect)
    header = next(reader, None)
    cols = _columns(header or [])
    if not cols:
        return []

    def cell(row, idxs):
        return " ".join(row[i].strip() for i in idxs if i < len(row) and row[i].strip())

    ref = date(edition_year, 7, 1)
    people = []
    for row in reader:
        name = cell(row, cols["name"]) or f"{cell(row, cols['first'])} {cell(row, cols['last'])}"
        birth = _parse_birthdate(cell(row, cols["birth"])) if cell(row, cols["birth"]) else None
        ids = person_keys(name, birth, ref)
        for e in EMAIL_RE.findall(cell(row, cols["email"])):
            ids.append({"type": "email", "hash": generate_repeat_hash(e.strip().lower())})
        if ids:
            people.append(ids)
    return people


def _entry(ids: List[Dict]) -> str:
    """Stable id for one list row: the same person re-imported keeps the same entry."""
    return min(i["hash"] for i in ids)[:16]


def import_legacy_list(series, label: str, edition_year: int, raw: str) -> Dict[str, int]:
    """
    Add the people / addresses in ``raw`` to the legacy edition (series,
    label, year), creating it if needed, then re-resolve the series.
    """
    from ..models import LegacyEdition, LegacyIdentity
    from .people import queue_recompute

    people = parse_people(raw, edition_year)
    rows: List[Tuple[str, str, str]] = []
    for ids in people:
        entry = _entry(ids)
        rows.extend((i["type"], i["hash"], entry) for i in ids)
    hashes = extract_hashes(raw)
    with transaction.atomic():
        edition, _created = LegacyEdition.objects.get_or_create(
            series=series, label=label.strip(), edition_year=edition_year,
        )
        before = _count(edition)
        # Person rows first, so e-mails on them keep their entry.
        LegacyIdentity.objects.bulk_create(
            [LegacyIdentity(legacy_edition=edition, identity_type=t, identity_hash=h, entry=e) for t, h, e in rows],
            ignore_conflicts=True, batch_size=1000,
        )
        LegacyIdentity.objects.bulk_create(
            [LegacyIdentity(legacy_edition=edition, identity_type="email", identity_hash=h) for h in hashes],
            ignore_conflicts=True, batch_size=1000,
        )
        after = _count(edition)
    queue_recompute(series.organizer_id, series.slug)
    return {"found": len(people) or len(hashes), "imported": after - before, "people": len(people),
            "edition_id": edition.pk}


def _count(edition) -> int:
    """People on a legacy list: one per row entry, plus plain e-mail rows."""
    ids = edition.identities
    return ids.exclude(entry="").values("entry").distinct().count() + ids.filter(entry="").count()
