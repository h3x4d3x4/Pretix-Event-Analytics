"""
Cohort retention matrix builder.

For a series, the share of each edition's participants who came back to
each later edition:

    {
        2022: {2023: 0.42, 2024: 0.31, 2026: 0.18},
        2023: {2024: 0.38, 2026: 0.22},
        2024: {2026: 0.15},
    }

Built on the resolved people of ``services.attendance`` — the same
identities that drive the "returning" KPIs — so the matrix and the KPIs can
no longer disagree. Editions that share a year are merged.
"""
from collections import OrderedDict
from typing import Dict

from .attendance import load_attendance


def _year_sets(series_slug: str, organizer_id: int, unit: str) -> "OrderedDict[int, set]":
    att = load_attendance(organizer_id, series_slug, unit)
    out: "OrderedDict[int, set]" = OrderedDict()
    for e in att.editions:
        out.setdefault(e.year, set()).update(att.sets.get(e.key, set()))
    return out


def build_cohort_matrix(series_slug: str, organizer_id: int, unit: str = "people") -> Dict[int, Dict[int, float]]:
    """
    :returns: {source_year: {target_year: retention_rate 0.0–1.0}}; empty
              when the series has fewer than two editions.
    """
    years = _year_sets(series_slug, organizer_id, unit)
    ordered = list(years)
    if len(ordered) < 2:
        return {}
    matrix: Dict[int, Dict[int, float]] = {}
    for i, src in enumerate(ordered):
        source = years[src]
        if not source:
            continue
        matrix[src] = {
            tgt: round(len(source & years[tgt]) / len(source), 4)
            for tgt in ordered[i + 1:]
        }
    return matrix


def get_cohort_sizes(series_slug: str, organizer_id: int, unit: str = "people") -> Dict[int, int]:
    """Distinct participants per edition year."""
    return {y: len(s) for y, s in _year_sets(series_slug, organizer_id, unit).items()}


def invalidate_cohort_cache(series_slug: str, organizer_id: int) -> None:
    """Kept for API compatibility — caches are now version-keyed."""
    from .versioning import bump

    bump(organizer_id)
