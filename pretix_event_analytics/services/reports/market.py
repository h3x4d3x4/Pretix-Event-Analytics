"""
Resale section: tickets that changed hands (TicketSwap + manual name
changes, counted once per ticket). Reads Pretix data read-only, so results
are cached.
"""
import logging
from typing import Dict

from django.utils.translation import gettext as _

from ..resale import resale_by_edition, resale_stats
from .charts import serie, spec
from .scope import ReportScope

logger = logging.getLogger(__name__)

CHANNEL_LABELS = (("ticketswap", "TicketSwap"), ("manual", _("Manual name change")), ("both", _("Both")))


def build(scope: ReportScope) -> Dict:
    return scope.cached("market", lambda: _build(scope))


def _build(scope: ReportScope) -> Dict:
    try:
        stats = resale_stats(scope.event)
    except Exception:
        logger.exception("analytics: resale stats failed for %s", scope.event.slug)
        return {"error": True}
    stats.pop("rows", None)
    out = {"stats": stats, "channel_rows": [
        {"key": k, "label": str(label), "count": stats["channels"][k],
         "share": round(stats["channels"][k] / stats["changed"] * 100, 1) if stats["changed"] else 0.0}
        for k, label in CHANNEL_LABELS
    ]}
    if stats["by_month"]:
        months = [r["month"] for r in stats["by_month"]]
        series = []
        if stats["swaps_dated"]:
            series.append(serie("TicketSwap", [r["ticketswap"] for r in stats["by_month"]]))
        series.append(serie(_("Manual name change"), [r["manual"] for r in stats["by_month"]]))
        out["monthly_chart"] = spec("bar", months, series, stacked=len(series) > 1, y_title=_("Tickets"))
    if scope.series and scope.can_see_series:
        try:
            by_edition = resale_by_edition(scope.series, scope.event.organizer)
        except Exception:
            logger.exception("analytics: resale by edition failed for %s", scope.event.slug)
            by_edition = []
        if len(by_edition) > 1 and any(r["changed"] for r in by_edition):
            out["edition_chart"] = spec("bar", [str(r["edition_year"]) for r in by_edition],
                                        [serie(_("Tickets that changed hands"), [r["rate"] for r in by_edition])],
                                        fmt="percent", y_title=_("Share of tickets"))
    return out
