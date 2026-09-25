"""
Series overview: every edition side by side (organizer level).
"""
from typing import Dict

from django.db.models import Count, Q, Sum
from django.utils.translation import gettext as _

from ...models import AnalyticsOrderFact, AnalyticsTicketFact
from ..attendance import load_attendance
from ..versioning import cached
from .charts import serie, spec
from .loyalty import _first_timer_cohorts, _flow, _label
from .scope import pct


def build(series, unit: str = "people") -> Dict:
    return cached(series.organizer_id, f"series:{series.pk}:{unit}", lambda: _build(series, unit))


def _build(series, unit: str) -> Dict:
    att = load_attendance(series.organizer_id, series.slug, unit)
    editions = att.editions
    event_ids = [e.event_id for e in editions if e.event_id]

    sold = Q(order_status="p", is_refunded=False)
    orders = {r["event_id"]: r for r in AnalyticsOrderFact.objects.filter(event_id__in=event_ids).values("event_id").annotate(
        orders=Count("id", filter=sold), revenue=Sum("total_gross", filter=sold),
        all=Count("id"), lost=Count("id", filter=Q(order_status="c") | Q(is_refunded=True)),
    )}
    tickets = {r["event_id"]: r for r in AnalyticsTicketFact.objects.filter(
        event_id__in=event_ids, is_addon=False, order_fact__order_status="p", order_fact__is_refunded=False,
    ).values("event_id").annotate(n=Count("id"), checked=Count("id", filter=Q(checked_in=True)))}

    flow = _flow(att, editions)
    rows = []
    for e, f in zip(editions, flow["rows"]):
        o = orders.get(e.event_id, {}) if e.event_id else {}
        t = tickets.get(e.event_id, {}) if e.event_id else {}
        rows.append({
            "label": _label(e, editions), "year": e.year, "legacy": e.is_legacy, "event_id": e.event_id,
            "on_sale": f["on_sale"],
            "orders": o.get("orders"), "revenue": float(o.get("revenue") or 0) if o else None,
            "tickets": t.get("n"), "people": f["total"],
            "first_time_pct": f["new_pct"] if f["total"] else None,
            "retention_pct": f["retention_pct"],
            "refund_pct": pct(o.get("lost", 0), o.get("all", 0)) if o else None,
            "checkin_pct": pct(t.get("checked", 0), t.get("n", 0)) if t and t.get("checked") else None,
            "unidentified": att.unidentified.get(e.key, 0),
        })

    real = [r for r in rows if not r["legacy"] and r["tickets"]]
    growth = spec("bar", [r["label"] for r in real], [serie(_("Tickets"), [r["tickets"] for r in real])],
                  y_title=_("Tickets")) if real else None
    return {
        "unit": unit,
        "rows": rows,
        "flow": flow,
        "first_cohorts": _first_timer_cohorts(att, editions),
        "growth_chart": growth,
        "people_total": len(att.all_people()),
    }
