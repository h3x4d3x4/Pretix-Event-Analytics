"""
Overview section: headline numbers with a like-for-like comparison against
the previous edition, plus the pace chart and a data-health summary.
"""
from typing import Dict, Optional

from django.db.models import Avg, Count, Q, Sum

from ...models import FACT_VERSION, AnalyticsOrderFact
from ..attendance import load_attendance
from . import sales
from .scope import ReportScope, pct


def build(scope: ReportScope) -> Dict:
    return scope.cached("overview", lambda: _build(scope))


def _delta(cur, prev) -> Optional[float]:
    if prev in (None, 0) or cur is None:
        return None
    return round((float(cur) - float(prev)) / float(prev) * 100, 1)


def _kpis(scope: ReportScope, orders) -> Dict:
    agg = orders.aggregate(
        revenue=Sum("total_gross"), orders=Count("id"), tickets=Sum("ticket_count"),
        aov=Avg("total_gross"), repeat=Count("id", filter=Q(is_repeat_buyer=True)),
        identified=Count("id", filter=~Q(person_key="")),
        groups=Count("id", filter=Q(is_group_order=True)),
    )
    return {k: (v or 0) for k, v in agg.items()}


def _build(scope: ReportScope) -> Dict:
    k = _kpis(scope, scope.orders)
    admissions = scope.admissions.aggregate(
        n=Count("id"), revenue=Sum("price"), checked=Count("id", filter=Q(checked_in=True)),
        identified=Count("id", filter=~Q(attendee_person_key="")),
        returning=Count("id", filter=Q(is_returning_attendee=True)),
    )
    status = scope.orders_any_status.aggregate(
        all=Count("id"),
        refunded=Count("id", filter=Q(order_status="c") | Q(is_refunded=True)),
        refunded_amount=Sum("refunded_amount"),
    )

    tiles = {
        "revenue": k["revenue"],
        "orders": k["orders"],
        "tickets": admissions["n"],
        "aov": k["aov"],
        "avg_ticket": (admissions["revenue"] or 0) / admissions["n"] if admissions["n"] else 0,
        "returning_buyers_pct": pct(k["repeat"], k["identified"]),
        "returning_buyers": k["repeat"],
        "group_pct": pct(k["groups"], k["orders"]),
        "refund_count": status["refunded"],
        "refund_pct": pct(status["refunded"], status["all"]),
        "refunded_amount": status["refunded_amount"] or 0,
        "checkin_pct": pct(admissions["checked"], admissions["n"]) if admissions["checked"] else None,
        "checked_in": admissions["checked"],
        "attendee_returning_pct": pct(admissions["returning"], admissions["identified"]),
    }

    out = {"tiles": tiles}

    # First-timers (series-wide people, independent of filters)
    if scope.series:
        att = load_attendance(scope.organizer_id, scope.series.slug, "people")
        key = f"e{scope.event.pk}"
        if key in att.sets:
            idx = [e.key for e in att.editions].index(key)
            prior = set().union(*[att.sets[e.key] for e in att.editions[:idx]]) if idx else set()
            cur = att.sets[key]
            out["first_timers"] = {
                "pct": pct(len(cur - prior), len(cur)),
                "count": len(cur - prior),
                "people": len(cur),
                "history_start": att.editions[0].year,
                "editions_before": idx,
            }

    # Like-for-like comparison with the previous edition
    prev = scope.previous_edition
    if prev and not scope.merged:
        pacing = sales._edition_curves(scope, [(prev, None), (scope.event, None)])
        point = scope.days_until_event() if scope.is_upcoming else 0
        point = max(point or 0, 0)
        cur_t = sales._cumulative_at(pacing[scope.event.pk]["tickets"], point)
        cur_r = sales._cumulative_at(pacing[scope.event.pk]["revenue"], point)
        prev_t = sales._cumulative_at(pacing[prev.pk]["tickets"], point)
        prev_r = sales._cumulative_at(pacing[prev.pk]["revenue"], point)
        out["compare"] = {
            "label": str(prev.name),
            "point": point,
            "same_point": scope.is_upcoming,
            "tickets": prev_t, "revenue": prev_r,
            "tickets_delta": _delta(cur_t, prev_t),
            "revenue_delta": _delta(cur_r, prev_r),
        }

    sales_data = sales.build(scope)
    out["pacing_tickets"] = sales_data.get("pacing_tickets")
    out["forecast"] = sales_data.get("forecast")

    # Data health
    facts = AnalyticsOrderFact.objects.filter(event_id__in=scope.event_ids)
    out["stale_facts"] = facts.filter(fact_version__lt=FACT_VERSION).count()
    out["has_data"] = facts.exists()
    out["coverage"] = {
        "identified_pct": pct(admissions["identified"], admissions["n"]),
        "unidentified": admissions["n"] - admissions["identified"],
    }
    return out
