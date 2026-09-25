"""
Operations section: check-in and no-shows, refunds and cancellations,
and the opt-in question breakdowns.
"""
import datetime
from collections import OrderedDict, defaultdict
from typing import Dict

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncHour, TruncWeek
from django.utils.translation import gettext as _

from ...models import AnalyticsAnswerFact
from .charts import serie, spec
from .scope import ReportScope, pct


def build(scope: ReportScope) -> Dict:
    return scope.cached("operations", lambda: _build(scope))


def _build(scope: ReportScope) -> Dict:
    return {
        "checkin": _checkin(scope),
        "refunds": _refunds(scope),
        "questions": _questions(scope),
        "funnel": _payment_funnel(scope) if not scope.merged else None,
    }


def _checkin(scope: ReportScope) -> Dict:
    adm = scope.admissions
    agg = adm.aggregate(n=Count("id"), checked=Count("id", filter=Q(checked_in=True)))
    if not agg["checked"]:
        return {"none": True, "tickets": agg["n"], "upcoming": scope.is_upcoming}
    out = {
        "tickets": agg["n"], "checked": agg["checked"], "rate": pct(agg["checked"], agg["n"]),
        "no_shows": agg["n"] - agg["checked"],
    }
    out["by_product"] = [{
        "name": r["item_name"], "tickets": r["n"], "checked": r["c"], "rate": pct(r["c"], r["n"]),
    } for r in adm.values("item_name").annotate(n=Count("id"), c=Count("id", filter=Q(checked_in=True))).order_by("-n")]
    seg = adm.aggregate(
        ret=Count("id", filter=Q(is_returning_attendee=True)),
        ret_c=Count("id", filter=Q(is_returning_attendee=True, checked_in=True)),
        new=Count("id", filter=Q(is_returning_attendee=False) & ~Q(attendee_person_key="")),
        new_c=Count("id", filter=Q(is_returning_attendee=False, checked_in=True) & ~Q(attendee_person_key="")),
    )
    out["by_type"] = [
        {"name": _("Returning attendees"), "tickets": seg["ret"], "checked": seg["ret_c"], "rate": pct(seg["ret_c"], seg["ret"])},
        {"name": _("First-time attendees"), "tickets": seg["new"], "checked": seg["new_c"], "rate": pct(seg["new_c"], seg["new"])},
    ]
    arrivals = list(adm.filter(checked_in=True).annotate(h=TruncHour("first_checkin_at", tzinfo=scope.tz))
                    .values("h").annotate(n=Count("id")).order_by("h"))
    if arrivals:
        # Continuous hourly axis so quiet hours are visible as gaps, not skipped.
        # Step in UTC so daylight-saving changes neither skip nor repeat an hour.
        utc = datetime.timezone.utc
        counts = {a["h"].astimezone(utc): a["n"] for a in arrivals}
        hours, cur, last = [], min(counts), max(counts)
        while cur <= last:
            hours.append(cur)
            cur += datetime.timedelta(hours=1)
        out["arrivals_chart"] = spec("bar", [scope.local(h).strftime("%a %H:00") for h in hours],
                                     [serie(_("First entries"), [counts.get(h, 0) for h in hours])],
                                     y_title=_("Attendees"))
    return out


def _refunds(scope: ReportScope) -> Dict:
    qs = scope.orders_any_status
    lost_q = Q(order_status="c") | Q(is_refunded=True)
    agg = qs.aggregate(
        all=Count("id"), lost=Count("id", filter=lost_q), lost_value=Sum("total_gross", filter=lost_q),
        refunded=Sum("refunded_amount"),
        partial=Count("id", filter=Q(refunded_amount__gt=0, is_refunded=False, order_status="p")),
    )
    out = {
        "orders": agg["all"], "canceled": agg["lost"], "rate": pct(agg["lost"], agg["all"]),
        "lost_value": float(agg["lost_value"] or 0), "refunded": float(agg["refunded"] or 0),
        "partial": agg["partial"],
    }
    if not agg["lost"]:
        return out
    weeks = {scope.local_date(w["w"]): w["n"] for w in qs.filter(lost_q).exclude(canceled_at__isnull=True)
             .annotate(w=TruncWeek("canceled_at", tzinfo=scope.tz)).values("w").annotate(n=Count("id"))}
    if weeks:
        first_order = qs.order_by("order_datetime").values_list("order_datetime", flat=True).first()
        start = scope.local_date(first_order) if first_order else min(weeks)
        cur = min(start - datetime.timedelta(days=start.weekday()), min(weeks))
        axis = []
        while cur <= max(weeks):
            axis.append(cur)
            cur += datetime.timedelta(days=7)
        out["weekly_chart"] = spec("bar", [d.strftime("%Y-%m-%d") for d in axis],
                                   [serie(_("Canceled orders"), [weeks.get(d, 0) for d in axis])],
                                   x_title=_("Week of cancellation"), y_title=_("Orders"))
    from ...models import AnalyticsTicketFact
    t_all = AnalyticsTicketFact.objects.filter(order_fact__in=qs, is_addon=False)
    out["by_product"] = [{
        "name": r["item_name"], "tickets": r["n"], "canceled": r["c"], "rate": pct(r["c"], r["n"]),
    } for r in t_all.values("item_name").annotate(
        n=Count("id"), c=Count("id", filter=Q(order_fact__order_status="c") | Q(order_fact__is_refunded=True)),
    ).order_by("-c", "-n") if r["n"]]
    return out


def _questions(scope: ReportScope) -> list:
    rows = list(AnalyticsAnswerFact.objects.filter(ticket_fact__order_fact__in=scope.orders)
                .values("question_id", "question_label", "answer_value", "ticket_fact__is_returning_attendee")
                .annotate(n=Count("id")))
    if not rows:
        return []
    by_q = OrderedDict()
    for r in sorted(rows, key=lambda r: (r["question_label"], r["question_id"])):
        q = by_q.setdefault(r["question_id"], {"label": r["question_label"], "answers": defaultdict(lambda: [0, 0])})
        q["answers"][r["answer_value"]][1 if r["ticket_fact__is_returning_attendee"] else 0] += r["n"]
    out = []
    for qid, q in by_q.items():
        answers = sorted(q["answers"].items(), key=lambda kv: -(kv[1][0] + kv[1][1]))
        total = sum(a[0] + a[1] for _k, a in answers)
        labels = [k for k, _v in answers]
        out.append({
            "label": q["label"],
            "total": total,
            "rows": [{"answer": k, "count": v[0] + v[1], "share": pct(v[0] + v[1], total),
                      "new": v[0], "returning": v[1]} for k, v in answers],
            "chart": spec("bar", labels, [
                serie(_("First-time"), [v[0] for _k, v in answers], slot=0),
                serie(_("Returning"), [v[1] for _k, v in answers], slot=1),
            ], stacked=True, horizontal=True),
        })
    return out


def _payment_funnel(scope: ReportScope) -> Dict:
    """
    Orders by payment method and how they ended: paid, still pending,
    expired unpaid, canceled. Read-only against Pretix's orders — the fact
    tables only hold orders that were paid.
    """
    from pretix.base.models import OrderPayment

    from ...forms import provider_label

    qs = OrderPayment.objects.filter(order__event=scope.event)
    f = scope.filters
    if f.get("date_from"):
        qs = qs.filter(order__datetime__gte=datetime.datetime.combine(f["date_from"], datetime.time.min, scope.tz))
    if f.get("date_to"):
        qs = qs.filter(order__datetime__lt=datetime.datetime.combine(
            f["date_to"] + datetime.timedelta(days=1), datetime.time.min, scope.tz))
    rows = defaultdict(lambda: {"p": 0, "n": 0, "e": 0, "c": 0})
    for r in qs.values("provider", "order__status").annotate(n=Count("order", distinct=True)):
        rows[r["provider"]][r["order__status"]] += r["n"]
    out = []
    for provider, c in rows.items():
        total = sum(c.values())
        if not total:
            continue
        out.append({
            "label": provider_label(provider), "total": total, "paid": c["p"], "pending": c["n"],
            "expired": c["e"], "canceled": c["c"], "conversion": pct(c["p"], total),
        })
    out.sort(key=lambda r: -r["total"])
    if not out:
        return None
    totals = {k: sum(r[k] for r in out) for k in ("total", "paid", "pending", "expired", "canceled")}
    totals["conversion"] = pct(totals["paid"], totals["total"])
    return {"rows": out, "totals": totals}
