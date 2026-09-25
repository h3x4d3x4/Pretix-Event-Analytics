"""
Loyalty section: first-timers vs returning, how often people come back,
and how any chosen set of editions overlaps.

All set comparisons run on the series attendance model
(``services.attendance``), i.e. on resolved people, not orders. Dashboard
filters (country, age, …) narrow the *segment* tables, which are fact
based; the set comparisons always cover the whole series so that "who came
back" is never silently answered for a subset.
"""
from collections import Counter
from typing import Dict, List, Optional

from django.db.models import Count, Q
from django.utils.translation import gettext as _

from ..attendance import UNITS, Attendance, load_attendance
from .charts import serie, spec
from .scope import ReportScope, pct

MAX_COMBOS = 15


def options(scope: ReportScope, att: Optional[Attendance] = None) -> Dict:
    unit = scope.params.get("unit", "people")
    unit = unit if unit in UNITS else "people"
    requested = scope.params.getlist("compare") if hasattr(scope.params, "getlist") else []
    return {"unit": unit, "compare": requested}


def build(scope: ReportScope) -> Dict:
    opts = options(scope)
    return scope.cached(f"loyalty:{opts['unit']}:{sorted(opts['compare'])}", lambda: _build(scope, opts))


def _build(scope: ReportScope, opts: Dict) -> Dict:
    if not scope.series:
        return {"no_series": True, "unit": opts["unit"]}
    if not scope.can_see_series:
        return {"no_permission": True, "unit": opts["unit"]}
    att = load_attendance(scope.organizer_id, scope.series.slug, opts["unit"])
    editions = att.editions
    if not editions:
        return {"no_series": True, "unit": opts["unit"]}
    keys = [e.key for e in editions]
    by_key = att.by_key()
    focus = f"e{scope.event.pk}"
    selected = [k for k in keys if k in opts["compare"]] or keys

    out = {
        "unit": opts["unit"],
        "editions": [{"key": e.key, "label": _label(e, editions), "year": e.year, "legacy": e.is_legacy,
                      "selected": e.key in selected, "is_focus": e.key == focus,
                      "size": len(att.sets[e.key])} for e in editions],
        "selected": selected,
        "history_start": editions[0].year,
        "has_legacy": any(e.is_legacy for e in editions),
    }
    if focus in att.sets:
        out["focus"] = _focus(att, focus)
    else:
        out["focus_inactive"] = True
    out.update(_frequency(att, selected, by_key, editions))
    out["flow"] = _flow(att, editions)
    out["first_cohorts"] = _first_timer_cohorts(att, editions)
    out["segments"] = _segments(scope, opts["unit"]) if focus in att.sets else []
    out["scores"] = _scores(scope)
    return out


def _label(e, editions) -> str:
    years = [x.year for x in editions]
    return str(e.year) if years.count(e.year) == 1 else f"{e.label} ({e.year})"


# ── The edition you are looking at ────────────────────────────────────────────

def _focus(att: Attendance, focus: str) -> Dict:
    editions = att.editions
    idx = [e.key for e in editions].index(focus)
    earlier = editions[:idx]
    current = att.sets[focus]
    prior_sets = [att.sets[e.key] for e in earlier]
    prior = set().union(*prior_sets) if prior_sets else set()
    prev = att.sets[earlier[-1].key] if earlier else set()

    returning = current & prior
    first_timers = current - prior
    from_last = current & prev
    after_gap = returning - prev
    lost = prev - current
    loyal = set.intersection(current, *prior_sets) if prior_sets else set()

    prior_counts = Counter(sum(1 for s in prior_sets if p in s) for p in current)
    buckets = [(0, _("First time")), (1, _("1 previous")), (2, _("2 previous")), (3, _("3 previous"))]
    labels, values = [], []
    for n, label in buckets:
        labels.append(label)
        values.append(prior_counts.get(n, 0))
    more = sum(v for k, v in prior_counts.items() if k >= 4)
    if more or len(earlier) >= 4:
        labels.append(_("4+ previous"))
        values.append(more)
    # Trim buckets that cannot exist yet (e.g. "3 previous" in the 2nd edition)
    usable = min(len(earlier), len(labels) - 1) + 1
    labels, values = labels[:usable], values[:usable]

    identified = len(current)
    unidentified = att.unidentified.get(focus, 0)
    return {
        "label": _label(editions[idx], editions),
        "participants": identified,
        "first_timers": len(first_timers),
        "first_timer_pct": pct(len(first_timers), identified),
        "returning": len(returning),
        "returning_pct": pct(len(returning), identified),
        "from_last": len(from_last),
        "from_last_pct": pct(len(from_last), identified),
        "after_gap": len(after_gap),
        "lost": len(lost) if earlier else None,
        "lost_pct": pct(len(lost), len(prev)) if prev else None,
        "prev_label": _label(earlier[-1], editions) if earlier else None,
        "prev_size": len(prev),
        "loyal_all": len(loyal) if len(earlier) >= 1 else None,
        "editions_so_far": idx + 1,
        "unidentified": unidentified,
        "coverage_pct": pct(identified, identified + unidentified) if unidentified else 100.0,
        "previous_chart": spec("bar", labels, [serie(_("People"), values)], y_title=_("People")),
        "previous_table": [{"label": l, "count": v, "share": pct(v, identified)} for l, v in zip(labels, values)],
    }


# ── Any set of editions ───────────────────────────────────────────────────────

def _frequency(att: Attendance, selected: List[str], by_key, editions) -> Dict:
    sets = {k: att.sets[k] for k in selected}
    union = set().union(*sets.values()) if sets else set()
    memberships = {}
    for p in union:
        memberships[p] = tuple(k for k in selected if p in sets[k])

    freq = Counter(len(m) for m in memberships.values())
    n_sel = len(selected)
    labels = [(_("1 edition") if i == 1 else _("%(n)s editions") % {"n": i}) for i in range(1, n_sel + 1)]
    values = [freq.get(i, 0) for i in range(1, n_sel + 1)]

    matrix = []
    for a in selected:
        row = {"label": _label(by_key[a], editions), "size": len(sets[a]), "cells": []}
        for b in selected:
            both = len(sets[a] & sets[b])
            share = pct(both, len(sets[a]))
            row["cells"].append({"count": both, "pct": share, "self": a == b, "level": _level(share)})
        matrix.append(row)

    combos = Counter(memberships.values()).most_common(MAX_COMBOS)
    combo_rows = [{
        "marks": [k in combo for k in selected],
        "count": n,
        "share": pct(n, len(union)),
        "label": " + ".join(_label(by_key[k], editions) for k in combo),
    } for combo, n in combos]

    all_of_them = sum(1 for m in memberships.values() if len(m) == n_sel) if n_sel > 1 else None
    return {
        "selected_labels": [_label(by_key[k], editions) for k in selected],
        "union": len(union),
        "attended_all": all_of_them,
        "frequency_chart": spec("bar", labels, [serie(_("People"), values)], y_title=_("People"),
                                x_title=_("Editions attended (of those selected)")),
        "frequency_table": [{"label": l, "count": v, "share": pct(v, len(union))} for l, v in zip(labels, values)],
        "overlap": matrix,
        "combos": combo_rows,
        "combos_hidden": max(0, len(set(memberships.values())) - MAX_COMBOS),
    }


def _level(share: float) -> int:
    return 0 if share <= 0 else max(1, min(6, round(share / 100 * 6)))


def _flow(att: Attendance, editions) -> Dict:
    """Per edition: first-timers, back from the previous edition, back after a gap; lost from previous."""
    from django.utils.timezone import now

    rows = []
    seen = set()
    prev_set = None
    today = now()
    for e in editions:
        cur = att.sets[e.key]
        if prev_set is None:
            new, from_prev, gap, lost = len(cur), 0, 0, None
        else:
            new = len(cur - seen)
            from_prev = len(cur & prev_set)
            gap = len((cur & seen) - prev_set)
            lost = len(prev_set - cur)
        rows.append({
            "label": _label(e, editions), "legacy": e.is_legacy, "total": len(cur),
            "on_sale": bool(e.date_from and e.date_from > today),
            "new": new, "from_prev": from_prev, "after_gap": gap, "lost": lost,
            "new_pct": pct(new, len(cur)),
            "retention_pct": pct(from_prev, len(prev_set)) if prev_set else None,
        })
        seen |= cur
        prev_set = cur
    labels = [r["label"] for r in rows]
    chart = spec("bar", labels, [
        serie(_("First time"), [r["new"] for r in rows], slot=0),
        serie(_("Back from previous edition"), [r["from_prev"] for r in rows], slot=1),
        serie(_("Back after a gap"), [r["after_gap"] for r in rows], slot=2),
    ], stacked=True, y_title=_("People"))
    return {"rows": rows, "chart": chart}


def _first_timer_cohorts(att: Attendance, editions) -> Dict:
    """Of the people who came for the first time in edition X, how many were at each later edition?"""
    first_seen = {}
    for e in editions:
        for p in att.sets[e.key]:
            first_seen.setdefault(p, e.key)
    cohorts = []
    for i, e in enumerate(editions[:-1]):
        members = {p for p, k in first_seen.items() if k == e.key}
        if not members:
            continue
        cells = []
        for later in editions[i + 1:]:
            back = len(members & att.sets[later.key])
            share = pct(back, len(members))
            cells.append({"label": _label(later, editions), "count": back, "pct": share, "level": _level(share)})
        # Row i only has columns for later editions; pad so cells line up.
        cohorts.append({"label": _label(e, editions), "size": len(members), "cells": cells, "pad": list(range(i))})
    return {"rows": cohorts, "columns": [_label(e, editions) for e in editions[1:]]}


# ── Who are the first-timers? (fact based, filters apply) ────────────────────

TIMING = [("6+ months", 181, None), ("1–6 months", 31, 180), ("Last month", 0, 30), ("After start", None, -1)]


def _segments(scope: ReportScope, unit: str) -> List[Dict]:
    out = []
    if unit == "people":
        base = scope.admissions.filter(event=scope.event).exclude(attendee_person_key="")
        new_q = Q(is_returning_attendee=False)
        dims = [
            (_("Product"), "item_name"),
            (_("Country"), "order_fact__country_code"),
            (_("Age"), "age_range"),
        ]
        timing_field = "order_fact__days_before_event"
    else:
        base = scope.orders.filter(event=scope.event).exclude(person_key="")
        new_q = Q(is_repeat_buyer=False)
        dims = [
            (_("Country"), "country_code"),
            (_("Age"), "age_range"),
            (_("Payment method"), "payment_provider"),
        ]
        timing_field = "days_before_event"

    from ...forms import AGE_BUCKETS, provider_label
    from .audience import country_name

    def label(field, value):
        if not value:
            return _("Unknown")
        if field.endswith("country_code"):
            return country_name(value)
        if field == "payment_provider":
            return provider_label(value)
        return value

    for title, field in dims:
        rows = list(base.values(field).annotate(total=Count("id"), new=Count("id", filter=new_q)).order_by("-total")[:12])
        if field == "age_range":
            rows.sort(key=lambda r: AGE_BUCKETS.index(r[field]) if r[field] in AGE_BUCKETS else 99)
        table = [{"label": label(field, r[field]), "total": r["total"], "new": r["new"],
                  "new_pct": pct(r["new"], r["total"])} for r in rows if r["total"]]
        if len(table) > 1:
            out.append({"title": _("By %(dim)s") % {"dim": title.lower()}, "column": title, "rows": table})

    timing_rows = []
    for label, lo, hi in TIMING:
        q = Q()
        if lo is not None:
            q &= Q(**{f"{timing_field}__gte": lo})
        if hi is not None:
            q &= Q(**{f"{timing_field}__lte": hi})
        agg = base.filter(q).aggregate(total=Count("id"), new=Count("id", filter=new_q))
        if agg["total"]:
            timing_rows.append({"label": _(label), "total": agg["total"], "new": agg["new"],
                                "new_pct": pct(agg["new"], agg["total"])})
    if len(timing_rows) > 1:
        out.append({"title": _("By purchase timing"), "column": _("Bought"), "rows": timing_rows})
    return out


def _scores(scope: ReportScope) -> Dict:
    rows = list(scope.orders.values("predicted_repeat_probability").annotate(n=Count("id"))
                .order_by("predicted_repeat_probability"))
    if not rows:
        return {}
    bands = [(0, 19), (20, 39), (40, 59), (60, 79), (80, 100)]
    labels = [f"{lo}–{hi}" for lo, hi in bands]
    values = [sum(r["n"] for r in rows if lo <= r["predicted_repeat_probability"] <= hi) for lo, hi in bands]
    total = sum(values)
    return {
        "chart": spec("bar", labels, [serie(_("Orders"), values)], x_title=_("Return score"), y_title=_("Orders")),
        "high": values[3] + values[4],
        "high_pct": pct(values[3] + values[4], total),
    }
