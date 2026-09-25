"""
Attendance read model: who took part in which edition of a series.

Built from the person keys written by ``services.people``. Everything
"loyalty" — cohort matrix, frequency distribution, overlaps, churn — is a
set operation over this structure, so it lives in one cached place.

Units
-----
``people``  buyers *and* identified ticket holders (the survey question
            "have you been here before?" is about people, not orders)
``buyers``  order e-mail / payment identities only (the classic
            "returning customer" view)

Legacy editions (imported attendee lists) count for both units.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Set

from .people import Edition, series_editions
from .versioning import cached

UNITS = ("people", "buyers")
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
    unit = unit if unit in UNITS else "people"
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
        orders = AnalyticsOrderFact.objects.filter(
            event_id__in=list(event_key), order_status="p", is_refunded=False,
        ).values_list("event_id", "person_key")
        for ev, k in orders.iterator(chunk_size=5000):
            if k:
                att.sets[event_key[ev]].add(k[:_SHORT])
            elif unit == "buyers":
                att.unidentified[event_key[ev]] += 1

        if unit == "people":
            tickets = AnalyticsTicketFact.objects.filter(
                event_id__in=list(event_key), is_addon=False,
                order_fact__order_status="p", order_fact__is_refunded=False,
            ).values_list("event_id", "attendee_person_key")
            for ev, k in tickets.iterator(chunk_size=5000):
                if k:
                    att.sets[event_key[ev]].add(k[:_SHORT])
                else:
                    att.unidentified[event_key[ev]] += 1

    legacy_key = {e.legacy_id: e.key for e in editions if e.is_legacy}
    if legacy_key:
        rows = LegacyIdentity.objects.filter(legacy_edition_id__in=list(legacy_key)).values_list(
            "legacy_edition_id", "person_key",
        )
        for le, k in rows.iterator(chunk_size=5000):
            if k:
                att.sets[legacy_key[le]].add(k[:_SHORT])
    return att


def short_key(person_key: str) -> str:
    return person_key[:_SHORT]
