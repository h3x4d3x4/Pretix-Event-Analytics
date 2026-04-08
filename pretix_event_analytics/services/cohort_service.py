"""
Cohort retention matrix builder.

For a given event series, calculates forward retention rates across all
editions.  Only paid, non-refunded orders with a valid repeat_hash are
included.

Example output:
    {
        2022: {2023: 0.42, 2024: 0.31, 2026: 0.18},
        2023: {2024: 0.38, 2026: 0.22},
        2024: {2026: 0.15},
    }

Performance notes:
  - Hashes are loaded into Python sets for O(1) intersection.
  - At 50k orders/edition with 64-char hashes ≈ 3.2MB per cohort.
    With 4 editions that's <15MB total — well within memory budget.
  - For extremely large series (10+ editions, 200k+ orders each), a
    DB-level intersection via INNER JOIN would be more memory-efficient.

Caching:
  - build_cohort_matrix and get_cohort_sizes results are cached for
    CACHE_TTL seconds.  Call invalidate_cohort_cache() after any resync.
"""
import logging
from typing import Dict

from django.core.cache import cache
from django.db.models import Count

logger = logging.getLogger(__name__)

CACHE_TTL = 60 * 60  # 60 minutes


def _cache_key(series_slug: str, organizer_id: int, suffix: str) -> str:
    return f"analytics_cohort_{organizer_id}_{series_slug}_{suffix}"


def build_cohort_matrix(series_slug: str, organizer_id: int) -> Dict[int, Dict[int, float]]:
    """
    Build the full forward-retention matrix for a series.

    :param series_slug: The series slug to analyse.
    :param organizer_id: Organizer PK (scopes to prevent cross-org leakage).
    :returns: Nested dict {source_year: {target_year: retention_rate}}.
              Retention rate is 0.0–1.0 (e.g. 0.42 = 42%).
    """
    key = _cache_key(series_slug, organizer_id, "matrix")
    try:
        cached = cache.get(key)
        if cached is not None:
            return cached
    except Exception:
        logger.debug("analytics: cache read failed for cohort matrix", exc_info=True)

    from ..models import AnalyticsOrderFact

    base_qs = AnalyticsOrderFact.objects.filter(
        organizer_id=organizer_id,
        series_slug=series_slug,
        order_status="p",
        is_refunded=False,
    ).exclude(repeat_hash="")

    edition_years = sorted(
        base_qs.values_list("edition_year", flat=True)
        .distinct()
        .exclude(edition_year__isnull=True)
    )

    if len(edition_years) < 2:
        return {}

    # Load distinct hashes per edition into Python sets
    cohorts: Dict[int, set] = {}
    for year in edition_years:
        cohorts[year] = set(
            base_qs.filter(edition_year=year)
            .values_list("repeat_hash", flat=True)
            .distinct()
        )

    # Build forward-looking matrix
    matrix: Dict[int, Dict[int, float]] = {}
    for i, source_year in enumerate(edition_years):
        source_cohort = cohorts[source_year]
        if not source_cohort:
            continue
        matrix[source_year] = {}
        for target_year in edition_years[i + 1:]:
            target_cohort = cohorts[target_year]
            if not target_cohort:
                matrix[source_year][target_year] = 0.0
                continue
            intersection_size = len(source_cohort & target_cohort)
            matrix[source_year][target_year] = round(
                intersection_size / len(source_cohort), 4
            )

    try:
        cache.set(key, matrix, CACHE_TTL)
    except Exception:
        logger.debug("analytics: cache write failed for cohort matrix", exc_info=True)
    return matrix


def get_cohort_sizes(series_slug: str, organizer_id: int) -> Dict[int, int]:
    """
    Return the number of distinct buyers per edition year.
    Used for the cohort matrix header row.
    """
    key = _cache_key(series_slug, organizer_id, "sizes")
    try:
        cached = cache.get(key)
        if cached is not None:
            return cached
    except Exception:
        logger.debug("analytics: cache read failed for cohort sizes", exc_info=True)

    from ..models import AnalyticsOrderFact

    rows = (
        AnalyticsOrderFact.objects.filter(
            organizer_id=organizer_id,
            series_slug=series_slug,
            order_status="p",
            is_refunded=False,
        )
        .exclude(repeat_hash="")
        .exclude(edition_year__isnull=True)
        .values("edition_year")
        .annotate(buyer_count=Count("repeat_hash", distinct=True))
        .order_by("edition_year")
    )
    result = {row["edition_year"]: row["buyer_count"] for row in rows}
    try:
        cache.set(key, result, CACHE_TTL)
    except Exception:
        logger.debug("analytics: cache write failed for cohort sizes", exc_info=True)
    return result


def invalidate_cohort_cache(series_slug: str, organizer_id: int) -> None:
    """Delete cached cohort data for a series. Call after any resync or new order."""
    try:
        cache.delete(_cache_key(series_slug, organizer_id, "matrix"))
        cache.delete(_cache_key(series_slug, organizer_id, "sizes"))
    except Exception:
        logger.debug("analytics: cache invalidation failed for cohort data", exc_info=True)
