"""
Attendance read model: who took part in which edition of a series.

Built from the person keys written by ``services.people``. Everything
"loyalty" — cohort matrix, frequency distribution, overlaps, churn — is a
set operation over this structure, so it lives in one cached place.

Units
-----
``people``  ticket holders matched with certainty on name + birth date
            (the survey question "have you been here before?" is about
            people, not orders). Tickets without a certain match are
            counted as unidentified, never guessed.
``buyers``  the order e-mail (the classic "returning customer" view)

Legacy editions (imported lists) count e-mail rows as buyers and name +
birth-date rows as people.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Set

from .people import Edition, series_editions
from .versioning import cached

UNITS = ("people", "buyers")
# Internal: people including probable matches, shown next to the certain figure.
PEOPLE_INCL = "people_incl"
# 16 hex chars = 64 bits: collision-free in practice and a quarter the size
# of the full key, which matters when caching sets for large series.
_SHORT = 16


@dataclass
class Attendance:
    unit: str
    editions: List[Edition]
    sets: Dict[str, Set[str]] = field(default_factory=dict)
    # Participations without any identity signal, per edition key.
    unidentified: Dict[str, int] = field(default_factory=dict)

    def edition(self, key: str) -> Edition:
        return next(e for e in self.editions if e.key == key)

    def by_key(self) -> Dict[str, Edition]:
        return {e.key: e for e in self.editions}

    def all_people(self) -> Set[str]:
        out: Set[str] = set()
        for s in self.sets.values():
            out |= s
        return out


def load_attendance(organizer_id: int, series_slug: str, unit: str = "people") -> Attendance:
    unit = unit if unit in UNITS + (PEOPLE_INCL,) else "people"
    return cached(organizer_id, f"attendance:{series_slug}:{unit}",
                  lambda: _build(organizer_id, series_slug, unit))


def _build(organizer_id: int, series_slug: str, unit: str) -> Attendance:
    from ..models import AnalyticsOrderFact, AnalyticsTicketFact, LegacyIdentity

    editions = [e for e in series_editions(organizer_id, series_slug) if e.active]
    att = Attendance(unit=unit, editions=editions)
    for e in editions:
        att.sets[e.key] = set()
        att.unidentified[e.key] = 0

    event_key = {e.event_id: e.key for e in editions if e.event_id}
    if event_key:
        if unit == "buyers":
            orders = AnalyticsOrderFact.objects.filter(
                event_id__in=list(event_key), order_status="p", is_refunded=False,
            ).values_list("event_id", "person_key")
            for ev, k in orders.iterator(chunk_size=5000):
                if k:
                    att.sets[event_key[ev]].add(k[:_SHORT])
                else:
                    att.unidentified[event_key[ev]] += 1
        else:
            tickets = AnalyticsTicketFact.objects.filter(
                event_id__in=list(event_key), is_addon=False,
                order_fact__order_status="p", order_fact__is_refunded=False,
            ).values_list("event_id", "attendee_person_key_incl" if unit == PEOPLE_INCL else "attendee_person_key")
            for ev, k in tickets.iterator(chunk_size=5000):
                if k:
                    att.sets[event_key[ev]].add(k[:_SHORT])
                else:
                    att.unidentified[event_key[ev]] += 1

    legacy_key = {e.legacy_id: e.key for e in editions if e.is_legacy}
    if legacy_key:
        rows = LegacyIdentity.objects.filter(legacy_edition_id__in=list(legacy_key))
        rows = rows.filter(identity_type="email") if unit == "buyers" else rows.exclude(identity_type="email")
        rows = rows.values_list("legacy_edition_id", "person_key")
        for le, k in rows.iterator(chunk_size=5000):
            if k:
                att.sets[legacy_key[le]].add(k[:_SHORT])
    return att


def short_key(person_key: str) -> str:
    return person_key[:_SHORT]


def first_timers(att: Attendance, focus: str) -> Dict[str, float]:
    """First-timers at edition ``focus``: {"count", "people", "pct"} (empty if not an edition)."""
    from .reports.scope import pct

    keys = [e.key for e in att.editions]
    if focus not in keys:
        return {}
    idx = keys.index(focus)
    prior = set().union(*[att.sets[k] for k in keys[:idx]]) if idx else set()
    cur = att.sets[focus]
    new = len(cur - prior)
    return {"count": new, "people": len(cur), "pct": pct(new, len(cur)),
            "returning_pct": pct(len(cur) - new, len(cur))}
