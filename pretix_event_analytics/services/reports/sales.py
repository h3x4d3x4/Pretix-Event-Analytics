"""
Sales section: tickets *and* revenue over time, edition pacing, forecast,
weekday × hour heatmap and purchase timing.

Tickets and revenue are always two aligned charts rather than one chart
with two y-axes — different units on one plot invite false comparisons.
"""
import datetime
from collections import defaultdict
from decimal import Decimal
from statistics import mean
from typing import Dict, List

from django.db.models import Count, Sum
from django.db.models.functions import (
    Coalesce, ExtractHour, ExtractIsoWeekDay, TruncDay, TruncMonth, TruncWeek,
)
from django.utils.translation import gettext as _

from .charts import OTHER_SLOT, assign_slots, serie, spec
from .scope import ReportScope

GRANULARITIES = ("day", "week", "month")
SPLITS = ("none", "product", "category", "buyer_type", "country")
BASES = ("order", "payment")
TIMING_BUCKETS = [  # (label, min_days_inclusive, max_days_inclusive)
    ("6+ months", 181, None), ("3–6 months", 91, 180), ("1–3 months", 31, 90),
    ("2–4 weeks", 15, 30), ("1–2 weeks", 8, 14), ("Last week", 0, 7), ("After start", None, -1),
]
PACING_WINDOW = 365


def _options(scope: ReportScope) -> Dict:
    p = scope.params
    g = p.get("granularity", "day")
    split = p.get("split", "none")
    basis = p.get("basis", "order")
    return {
        "granularity": g if g in GRANULARITIES else "day",
        "split": split if split in SPLITS else "none",
        "basis": basis if basis in BASES else "order",
    }


def _trunc(granularity, field, tz):
    fn = {"day": TruncDay, "week": TruncWeek, "month": TruncMonth}[granularity]
    return fn(field, tzinfo=tz)


def _bucket_range(first: datetime.date, last: datetime.date, granularity: str) -> List[datetime.date]:
    out = []
    cur = first
    while cur <= last:
        out.append(cur)
        if granularity == "day":
            cur += datetime.timedelta(days=1)
        elif granularity == "week":
            cur += datetime.timedelta(days=7)
        else:
            cur = (cur.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return out


def _bucket_label(d: datetime.date, granularity: str) -> str:
    return d.strftime("%Y-%m") if granularity == "month" else d.strftime("%Y-%m-%d")


def build(scope: ReportScope) -> Dict:
    opts = _options(scope)
    return scope.cached(f"sales:{opts}", lambda: _build(scope, opts))


def _build(scope: ReportScope, opts: Dict) -> Dict:
    out = {"options": opts}
    out.update(_timeseries(scope, opts))
    out.update(_pacing(scope))
    out["heatmap"] = _heatmap(scope)
    out["timing"] = _timing(scope)
    return out


# ── Over time ─────────────────────────────────────────────────────────────────

def _timeseries(scope: ReportScope, opts: Dict) -> Dict:
    g, split, basis = opts["granularity"], opts["split"], opts["basis"]
    tz = scope.tz
    date_field = "order_datetime" if basis == "order" else Coalesce("payment_datetime", "order_datetime")
    t_date_field = ("order_fact__order_datetime" if basis == "order"
                    else Coalesce("order_fact__payment_datetime", "order_fact__order_datetime"))

    orders = scope.orders.annotate(bucket=_trunc(g, date_field, tz))
    tickets = scope.admissions.annotate(bucket=_trunc(g, t_date_field, tz))
    positions = scope.tickets.annotate(bucket=_trunc(g, t_date_field, tz))

    totals = {
        r["bucket"]: r for r in orders.values("bucket").annotate(
            orders=Count("id"), revenue=Sum("total_gross"),
        )
    }
    ticket_totals = {r["bucket"]: r["n"] for r in tickets.values("bucket").annotate(n=Count("id"))}
    buckets_present = [b for b in set(totals) | set(ticket_totals) if b is not None]
    if not buckets_present:
        return {"series_empty": True}

    def as_date(b):
        return b.astimezone(tz).date() if isinstance(b, datetime.datetime) else b

    buckets = _bucket_range(min(map(as_date, buckets_present)), max(map(as_date, buckets_present)), g)
    labels = [_bucket_label(b, g) for b in buckets]
    index = {b: i for i, b in enumerate(buckets)}

    def to_index(b):
        return index.get(as_date(b))

    # Split dimension → (ticket rows, revenue rows) keyed by (bucket, group)
    if split == "none":
        t_groups = {_("Tickets"): [0] * len(buckets)}
        r_groups = {_("Revenue"): [0.0] * len(buckets)}
        for b, n in ticket_totals.items():
            if to_index(b) is not None:
                t_groups[_("Tickets")][to_index(b)] = n
        for b, r in totals.items():
            if to_index(b) is not None:
                r_groups[_("Revenue")][to_index(b)] = float(r["revenue"] or 0)
        t_order = r_order = None
    else:
        if split == "product":
            t_key, r_qs, r_key, r_val = "item_name", positions, "item_name", Sum("price")
        elif split == "category":
            t_key, r_qs, r_key, r_val = "item_category", positions, "item_category", Sum("price")
        elif split == "buyer_type":
            t_key, r_qs, r_key, r_val = "is_returning_attendee", orders, "is_repeat_buyer", Sum("total_gross")
        else:  # country
            t_key, r_qs, r_key, r_val = "order_fact__country_code", orders, "country_code", Sum("total_gross")

        t_rows = list(tickets.values("bucket", t_key).annotate(n=Count("id")))
        r_rows = list(r_qs.values("bucket", r_key).annotate(v=r_val))

        def label_of(v, field):
            if field in ("is_returning_attendee", "is_repeat_buyer"):
                return _("Returning") if v else _("First-time")
            if field in ("item_category",) and not v:
                return _("Uncategorised")
            if field in ("order_fact__country_code", "country_code") and not v:
                return _("Unknown")
            return str(v)

        # Rank groups by overall tickets so slots are stable and "Other" absorbs the tail.
        rank = defaultdict(float)
        for r in t_rows:
            rank[label_of(r[t_key], t_key)] += r["n"]
        for r in r_rows:
            rank.setdefault(label_of(r[r_key], r_key), 0)
        if split == "buyer_type":
            ordered = [_("First-time"), _("Returning")]
        else:
            ordered = [k for k, _v in sorted(rank.items(), key=lambda kv: -kv[1])]
        slots = assign_slots(ordered)
        t_order = [k for k in ordered if slots[k] != OTHER_SLOT] + (
            [_("Other")] if OTHER_SLOT in slots.values() else [])
        r_order = t_order

        def group_of(label):
            return label if slots.get(label) != OTHER_SLOT else _("Other")

        t_groups = {k: [0] * len(buckets) for k in t_order}
        r_groups = {k: [0.0] * len(buckets) for k in r_order}
        for r in t_rows:
            i = to_index(r["bucket"])
            if i is not None:
                t_groups[group_of(label_of(r[t_key], t_key))][i] += r["n"]
        for r in r_rows:
            i = to_index(r["bucket"])
            if i is not None:
                r_groups[group_of(label_of(r[r_key], r_key))][i] += float(r["v"] or 0)

    def series_from(groups, order):
        # Slots come from the shared ranking (colour follows the entity);
        # series with no values in this chart are left out of it.
        names = order or list(groups)
        return [serie(n, groups[n], slot=(i if n != _("Other") else OTHER_SLOT))
                for i, n in enumerate(names) if any(groups[n])]

    # Date order, notes before tier changes on the same day.
    markers = sorted(_annotation_markers(scope, g, set(labels)) + _price_markers(scope, g, set(labels)),
                     key=lambda m: (m["x"], m.get("kind") != "note"))
    tickets_chart = spec("bar", labels, series_from(t_groups, t_order), stacked=True, cumulative=True,
                         y_title=_("Tickets"), markers=markers)
    revenue_chart = spec("bar", labels, series_from(r_groups, r_order), fmt="currency", stacked=True,
                         cumulative=True, y_title=_("Revenue"), markers=markers)

    table = []
    cum_t = cum_r = 0
    totals_by_date = {as_date(k): v for k, v in totals.items() if k is not None}
    for i, b in enumerate(buckets):
        row = totals_by_date.get(b)
        t = sum(g_[i] for g_ in t_groups.values())
        r = float(row["revenue"] or 0) if row else 0.0
        cum_t += t
        cum_r += r
        if t or r:
            table.append({"label": labels[i], "orders": row["orders"] if row else 0, "tickets": t,
                          "revenue": r, "cum_tickets": cum_t, "cum_revenue": cum_r})

    return {
        "tickets_chart": tickets_chart,
        "revenue_chart": revenue_chart,
        "series_table": table,
        "revenue_note": (
            _("Revenue split by product counts ticket and add-on prices only; fees are not attributed to a product.")
            if split in ("product", "category") else ""
        ),
    }


def _price_markers(scope: ReportScope, granularity: str, labels: set) -> List[Dict]:
    """
    Markers where an admission product moved to a new price tier
    (early bird → regular → late). Voucher sales are ignored, variations are
    tracked separately, and only *sustained* price runs count, so a single
    discounted or corrected ticket never produces a marker.
    """
    rows = (
        scope.admissions.filter(voucher_code="")
        .values_list("item_name", "variation_name", "price", "order_fact__order_datetime")
        .order_by("order_fact__order_datetime")
    )
    timelines = defaultdict(list)
    for name, variation, price, when in rows.iterator(chunk_size=2000):
        timelines[(name, variation)].append((when, price))

    by_label = defaultdict(list)
    ranked = sorted(timelines.items(), key=lambda kv: -len(kv[1]))[:4]
    for (name, variation), sales in ranked:
        min_run = max(3, len(sales) // 20)
        runs = []  # [start_datetime, price, length]
        for when, price in sales:
            if runs and runs[-1][1] == price:
                runs[-1][2] += 1
            else:
                runs.append([when, price, 1])
        kept = [r for r in runs if r[2] >= min_run]
        title = f"{name} ({variation})" if variation else name
        for prev, cur in zip(kept, kept[1:]):
            if prev[1] == cur[1]:
                continue
            d = scope.local_date(cur[0])
            if granularity == "month":
                label = d.strftime("%Y-%m")
            elif granularity == "week":
                label = (d - datetime.timedelta(days=d.weekday())).strftime("%Y-%m-%d")
            else:
                label = d.strftime("%Y-%m-%d")
            if label in labels:
                by_label[label].append(f"{title} {_fmt_money(prev[1])} → {_fmt_money(cur[1])}")
    return [{"x": label, "text": "; ".join(texts)} for label, texts in sorted(by_label.items())][:10]


def _bucket_of(d: datetime.date, granularity: str) -> str:
    if granularity == "month":
        return d.strftime("%Y-%m")
    if granularity == "week":
        return (d - datetime.timedelta(days=d.weekday())).strftime("%Y-%m-%d")
    return d.strftime("%Y-%m-%d")


def _annotation_markers(scope: ReportScope, granularity: str, labels: set) -> List[Dict]:
    """Organiser notes ("line-up announced") for the editions in view."""
    from ...models import SalesAnnotation

    by_label = defaultdict(list)
    for a in SalesAnnotation.objects.filter(event_id__in=scope.event_ids).order_by("date"):
        label = _bucket_of(a.date, granularity)
        if label in labels:
            by_label[label].append(a.label)
    return [{"x": label, "text": "; ".join(texts), "kind": "note"} for label, texts in sorted(by_label.items())]


def _fmt_money(v) -> str:
    v = Decimal(v or 0)
    return f"{v:.0f}" if v == v.to_integral_value() else f"{v:.2f}"


# ── Edition pacing & forecast ────────────────────────────────────────────────

def _edition_curves(scope: ReportScope, events: list) -> Dict[int, Dict]:
    """{event_id: {"tickets": {days_before: n}, "revenue": {days_before: v}}} with filters applied."""
    ids = [e.pk for e, _c in events]
    qs = scope.orders_for(ids).exclude(days_before_event__isnull=True)
    curves = {i: {"tickets": defaultdict(int), "revenue": defaultdict(float)} for i in ids}
    for r in qs.values("event_id", "days_before_event").annotate(t=Sum("ticket_count"), v=Sum("total_gross")):
        d = max(r["days_before_event"], 0)
        curves[r["event_id"]]["tickets"][d] += r["t"] or 0
        curves[r["event_id"]]["revenue"][d] += float(r["v"] or 0)
    return curves


def _days_left(scope: ReportScope, event):
    from django.utils.timezone import now

    if not event.date_from or event.date_from <= now():
        return None
    return (scope.local_date(event.date_from) - scope.local_date(now())).days


def _pacing_notes(scope: ReportScope, events: list, window: int) -> List[Dict]:
    from ...models import SalesAnnotation

    labels = {ev.pk: str(cfg.edition_year) for ev, cfg in events}
    starts = {ev.pk: scope.local_date(ev.date_from) for ev, _c in events if ev.date_from}
    notes = []
    for a in SalesAnnotation.objects.filter(event_id__in=list(starts)).order_by("date"):
        days_before = (starts[a.event_id] - a.date).days
        if 0 <= days_before <= window:
            notes.append({"x": -days_before, "text": f"{labels[a.event_id]}: {a.label}", "kind": "note",
                          "label": f"T–{days_before}"})
    return notes[:20]


def _cumulative_at(curve: Dict[int, float], days_before: int) -> float:
    """Total sold by the time there were ``days_before`` days left."""
    return sum(v for d, v in curve.items() if d >= days_before)


def _pacing(scope: ReportScope) -> Dict:
    events = scope.series_events
    if not events:
        return {}
    curves = _edition_curves(scope, events)
    window = min(PACING_WINDOW, max(
        (max(c["tickets"], default=0) for c in curves.values()), default=0))
    if window <= 0 and not any(c["tickets"] for c in curves.values()):
        return {}

    xs = list(range(-window, 1))
    t_series, r_series = [], []
    highlight = None
    today_left = scope.days_until_event() if scope.is_upcoming else None
    for slot, (ev, cfg) in enumerate(events):
        c = curves[ev.pk]
        if not c["tickets"]:
            continue
        label = f"{cfg.edition_year}" if len({cf.edition_year for _e, cf in events}) == len(events) else str(ev.name)
        t_vals, r_vals = [], []
        # Editions still on sale stop at "today" — no fake flat line into the future.
        left = _days_left(scope, ev)
        for x in xs:
            days_before = -x
            if left is not None and days_before < left:
                t_vals.append(None)
                r_vals.append(None)
                continue
            t_vals.append(_cumulative_at(c["tickets"], days_before))
            r_vals.append(round(_cumulative_at(c["revenue"], days_before), 2))
        if ev.pk == scope.event.pk:
            highlight = len(t_series)
        t_series.append(serie(label, t_vals, slot=slot % 7))
        r_series.append(serie(label, r_vals, slot=slot % 7))

    if len(t_series) == 0:
        return {}
    x_title = _("Days before the event")
    notes = _pacing_notes(scope, events, window)
    out = {
        "pacing_tickets": spec("line", xs, t_series, x="linear", x_title=x_title, y_title=_("Tickets sold"),
                               highlight=highlight, markers=notes),
        "pacing_revenue": spec("line", xs, r_series, fmt="currency", x="linear", x_title=x_title,
                               y_title=_("Revenue"), highlight=highlight, markers=notes),
    }

    # Same-point comparison: where did each edition stand with as many days left?
    point = today_left if today_left is not None and today_left >= 0 else 0
    rows = []
    current = None
    for ev, cfg in events:
        c = curves[ev.pk]
        if not c["tickets"]:
            continue
        final_t = _cumulative_at(c["tickets"], 0) if _days_left(scope, ev) is None else None
        final_r = _cumulative_at(c["revenue"], 0) if final_t is not None else None
        at_t = _cumulative_at(c["tickets"], point)
        at_r = _cumulative_at(c["revenue"], point)
        row = {
            "event_id": ev.pk, "label": str(ev.name), "year": cfg.edition_year, "is_current": ev.pk == scope.event.pk,
            "at_tickets": at_t, "at_revenue": at_r, "final_tickets": final_t, "final_revenue": final_r,
            "share_at_point": round(at_t / final_t * 100, 1) if final_t else None,
        }
        rows.append(row)
        if row["is_current"]:
            current = row
    for r in rows:
        if current and not r["is_current"] and r["at_tickets"]:
            r["delta_tickets_pct"] = round((current["at_tickets"] - r["at_tickets"]) / r["at_tickets"] * 100, 1)
    out["pacing_point"] = point
    out["pacing_table"] = rows
    out["forecast"] = _forecast(scope, rows, current) if scope.is_upcoming and not scope.merged else None
    return out


def _forecast(scope: ReportScope, rows: List[Dict], current) -> Dict:
    """
    Project final sales from how previous editions grew from the same point
    in their own sales cycle. Deliberately simple and explainable: one ratio
    per past edition, reported as a range.
    """
    if not current or not current["at_tickets"]:
        return None
    ratios_t, ratios_r, basis = [], [], []
    for r in rows:
        if r["is_current"] or not r["final_tickets"] or not r["at_tickets"]:
            continue
        ratios_t.append(r["final_tickets"] / r["at_tickets"])
        if r["at_revenue"]:
            ratios_r.append(r["final_revenue"] / r["at_revenue"])
        basis.append(r["year"])
    if not ratios_t:
        return None
    cfg = scope.config
    f = {
        "tickets": round(current["at_tickets"] * mean(ratios_t)),
        "tickets_low": round(current["at_tickets"] * min(ratios_t)),
        "tickets_high": round(current["at_tickets"] * max(ratios_t)),
        "revenue": round(current["at_revenue"] * mean(ratios_r), 2) if ratios_r else None,
        "revenue_low": round(current["at_revenue"] * min(ratios_r), 2) if ratios_r else None,
        "revenue_high": round(current["at_revenue"] * max(ratios_r), 2) if ratios_r else None,
        "basis": basis,
        "days_left": scope.days_until_event(),
        "ticket_target": cfg.ticket_target if cfg else None,
        "revenue_target": float(cfg.revenue_target) if cfg and cfg.revenue_target else None,
    }
    if f["ticket_target"]:
        f["ticket_target_pct"] = round(current["at_tickets"] / f["ticket_target"] * 100, 1)
        f["tickets_forecast_target_pct"] = round(f["tickets"] / f["ticket_target"] * 100, 1)
    if f["revenue_target"]:
        f["revenue_target_pct"] = round(current["at_revenue"] / f["revenue_target"] * 100, 1)
    return f


# ── When do people buy ────────────────────────────────────────────────────────

def _heatmap(scope: ReportScope) -> Dict:
    rows = (
        scope.orders.annotate(dow=ExtractIsoWeekDay("order_datetime", tzinfo=scope.tz),
                              hour=ExtractHour("order_datetime", tzinfo=scope.tz))
        .values("dow", "hour").annotate(n=Count("id"))
    )
    grid = [[0] * 24 for _i in range(7)]
    for r in rows:
        grid[r["dow"] - 1][r["hour"]] += r["n"]
    peak = max((max(row) for row in grid), default=0)
    days = [_("Mon"), _("Tue"), _("Wed"), _("Thu"), _("Fri"), _("Sat"), _("Sun")]
    return {
        "rows": [{"day": days[i], "cells": [{"n": n, "level": _level(n, peak), "hour": h}
                                            for h, n in enumerate(grid[i])]} for i in range(7)],
        "peak": peak,
        "timezone": str(scope.tz),
    }


def _level(n: int, peak: int) -> int:
    """0–6 step on the sequential ramp; 0 is 'none'."""
    if not n or not peak:
        return 0
    return max(1, min(6, round(n / peak * 6)))


def _timing(scope: ReportScope) -> Dict:
    rows = scope.orders.exclude(days_before_event__isnull=True).values("days_before_event").annotate(
        t=Sum("ticket_count"), v=Sum("total_gross"), o=Count("id"),
    )
    agg = {label: {"tickets": 0, "revenue": 0.0, "orders": 0} for label, _a, _b in TIMING_BUCKETS}
    for r in rows:
        d = r["days_before_event"]
        for label, lo, hi in TIMING_BUCKETS:
            if (lo is None or d >= lo) and (hi is None or d <= hi):
                agg[label]["tickets"] += r["t"] or 0
                agg[label]["revenue"] += float(r["v"] or 0)
                agg[label]["orders"] += r["o"]
                break
    labels = [_(label) for label, _a, _b in TIMING_BUCKETS if agg[label]["orders"] or label != "After start"]
    keys = [label for label, _a, _b in TIMING_BUCKETS if agg[label]["orders"] or label != "After start"]
    total_t = sum(a["tickets"] for a in agg.values())
    return {
        "chart": spec("bar", labels, [serie(_("Tickets"), [agg[k]["tickets"] for k in keys])],
                      y_title=_("Tickets")),
        "table": [{"label": _(k), **agg[k], "share": round(agg[k]["tickets"] / total_t * 100, 1) if total_t else 0}
                  for k in keys],
    }
