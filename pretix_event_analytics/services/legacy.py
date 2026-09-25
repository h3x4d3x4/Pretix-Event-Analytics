"""
Legacy editions: past attendee lists that predate Pretix or this plugin.

The organiser uploads (or pastes) any file that contains e-mail addresses —
a mailing-list export, an old ticketing CSV. Addresses are extracted with a
pattern match, normalised and HMAC-hashed in memory; only the hashes are
stored. The uploaded content is never written anywhere.
"""
import re
from typing import Dict

from django.db import transaction

from .hash_service import generate_repeat_hash

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-']+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def extract_hashes(raw: str) -> set:
    return {generate_repeat_hash(m.strip().lower()) for m in EMAIL_RE.findall(raw or "")} - {""}


def import_legacy_list(series, label: str, edition_year: int, raw: str) -> Dict[str, int]:
    """
    Add the addresses in ``raw`` to the legacy edition (series, label, year),
    creating it if needed, then re-resolve returning people for the series.
    """
    from ..models import LegacyEdition, LegacyIdentity
    from .people import recompute_series

    hashes = extract_hashes(raw)
    with transaction.atomic():
        edition, _created = LegacyEdition.objects.get_or_create(
            series=series, label=label.strip(), edition_year=edition_year,
        )
        before = edition.identities.count()
        LegacyIdentity.objects.bulk_create(
            [LegacyIdentity(legacy_edition=edition, identity_type="email", identity_hash=h) for h in hashes],
            ignore_conflicts=True, batch_size=1000,
        )
        after = edition.identities.count()
    recompute_series(series.organizer_id, series.slug)
    return {"found": len(hashes), "imported": after - before, "edition_id": edition.pk}
