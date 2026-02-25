"""
Repeat buyer detector.

Determines whether a buyer (identified only by their repeat_hash) has attended
a previous edition of the same event series.

All queries are scoped to:
  - Same organizer (prevent cross-organizer false matches)
  - Same series slug
  - edition_year < current event's edition year
  - Paid orders that are NOT refunded
"""
from typing import Optional

from ..models import AnalyticsOrderFact, EventAnalyticsConfig


def evaluate_repeat_status(event, identities: list[dict]) -> dict:
    """
    Check previous editions for matching identities (Stripe cards, Name+DOB, Emails). 
    Returns the "best" match—meaning if ANY of the included identities attended 
    a previous edition, the overall order is flagged as a repeat.

    :param event: Pretix Event instance.
    :param identities: List of identity dictionaries: {'type': str, 'hash': str}
    :returns: Dictionary with repeat status fields ready to store on AnalyticsOrderFact.
    """
    default = {
        "is_repeat_buyer": False,
        "repeat_from_last_edition": False,
        "repeat_from_any_previous": False,
        "repeat_count": 0,
        "first_seen_edition_year": None,
    }

    if not identities:
        return default

    try:
        config = EventAnalyticsConfig.objects.select_related("series").get(event=event)
    except EventAnalyticsConfig.DoesNotExist:
        return default

    if not config.series:
        return default

    # Build Q objects to match any identity type+hash combination
    from django.db.models import Q
    identity_query = Q()
    for identity in identities:
        identity_query |= Q(
            identities__identity_type=identity["type"], 
            identities__identity_hash=identity["hash"]
        )

    # Find all previous-edition facts for ANY of these identities within the series
    previous_facts = (
        AnalyticsOrderFact.objects.filter(
            identity_query,
            organizer_id=event.organizer_id,
            series_slug=config.series.slug,
            edition_year__lt=config.edition_year,
            order_status="p",    # paid
            is_refunded=False,
        )
        .values("edition_year")
        .distinct()
        .order_by("edition_year")
    )

    previous_years = [row["edition_year"] for row in previous_facts]

    if not previous_years:
        return default

    last_edition_year = _get_last_edition_year(
        config.series.slug, event.organizer_id, config.edition_year
    )

    return {
        "is_repeat_buyer": True,
        "repeat_from_any_previous": True,
        "repeat_from_last_edition": (
            last_edition_year is not None and last_edition_year in previous_years
        ),
        "repeat_count": len(previous_years),
        "first_seen_edition_year": previous_years[0],
    }


def _get_last_edition_year(
    series_slug: str, organizer_id: int, current_year: int
) -> Optional[int]:
    """
    Return the most recent edition year in the series before current_year,
    based on what is actually present in the analytics table.
    """
    result = (
        AnalyticsOrderFact.objects.filter(
            organizer_id=organizer_id,
            series_slug=series_slug,
            edition_year__lt=current_year,
            order_status="p",
            is_refunded=False,
        )
        .values_list("edition_year", flat=True)
        .distinct()
        .order_by("-edition_year")
        .first()
    )
    return result
