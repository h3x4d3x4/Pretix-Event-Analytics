"""
Resale section: attendee name changes as a signal of secondary-market
activity. Reads Pretix's audit log (read-only), so results are cached.
"""
import logging
from typing import Dict

from django.utils.translation import gettext as _

from ..secondary_market import get_name_change_stats, get_name_changes_by_edition
from .charts import serie, spec
from .scope import ReportScope

logger = logging.getLogger(__name__)


def build(scope: ReportScope) -> Dict:
    return scope.cached("market", lambda: _build(scope))


def _build(scope: ReportScope) -> Dict:
    try:
        stats = get_name_change_stats(scope.event)
    except Exception:
        logger.exception("analytics: secondary market stats failed for %s", scope.event.slug)
        stats = {"total_name_changes": 0, "orders_with_changes": 0, "total_orders": 0,
                 "name_change_rate": 0.0, "changes_by_month": []}
    out = {"stats": stats}
    if stats["changes_by_month"]:
        out["monthly_chart"] = spec("bar", [r["month"] for r in stats["changes_by_month"]],
                                    [serie(_("Name changes"), [r["count"] for r in stats["changes_by_month"]])],
                                    y_title=_("Name changes"))
    if scope.series:
        try:
            by_edition = get_name_changes_by_edition(scope.series, scope.event.organizer)
        except Exception:
            logger.exception("analytics: cross-edition name changes failed for %s", scope.event.slug)
            by_edition = []
        if by_edition:
            out["by_edition"] = by_edition
            out["edition_chart"] = spec("bar", [str(r["edition_year"]) for r in by_edition],
                                        [serie(_("Orders with a name change"), [r["name_change_rate"] for r in by_edition])],
                                        fmt="percent", y_title=_("Share of orders"))
    return out
