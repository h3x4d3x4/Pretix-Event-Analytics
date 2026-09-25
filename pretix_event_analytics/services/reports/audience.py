"""
Audience section: where buyers come from, how old they are, how they pay,
when they buy (personas) and how they group.
"""
from typing import Dict

import pycountry
from django.db.models import Avg, Case, CharField, Count, F, Min, Q, Sum, Value, When
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from ...forms import AGE_BUCKETS, provider_label
from .charts import fold_other, serie, spec
from .scope import ReportScope, pct

LAST_MINUTE_DAYS = 30


def build(scope: ReportScope) -> Dict:
    return scope.cached("audience", lambda: _build(scope))


def country_name(code: str) -> str:
    if not code:
        return _("Unknown")
    c = pycountry.countries.get(alpha_2=code.upper())
    return c.name if c else code


def country_flag(code: str) -> str:
    if not code or len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(ord(c) + 127397) for c in code.upper())


SOURCE_LABELS = {
    "question": _lazy("Residence question"),
    "invoice": _lazy("Invoice address"),
    "paypal_address": _lazy("PayPal address"),
    "paypal_account": _lazy("PayPal account"),
    "card_billing": _lazy("Card billing address"),
    "id_document": _lazy("ID document"),
    "iban": _lazy("Bank account (IBAN)"),
    "card_issuer": _lazy("Card's issuing bank"),
    "other_order": _lazy("Same customer's other orders"),
    "email_domain": _lazy("E-mail country domain"),
    "": _lazy("Unknown"),
}
COUNTRY_MODES = ("exact", "inferred", "probable")
# Sources that describe the bank or document rather than where the buyer lives.
INDIRECT_SOURCES = ("id_document", "iban", "card_issuer")


def _country_sources(orders, total):
    counts = dict(orders.exclude(country_source="").values_list("country_source").annotate(n=Count("id")))
    derived = dict(orders.filter(country_source="").exclude(country_inferred_source="").values_list(
        "country_inferred_source").annotate(n=Count("id")))
    counts.update(derived)
    counts[""] = orders.filter(country_source="", country_inferred_source="").count()
    order = list(SOURCE_LABELS)
    return [{"source": s, "label": str(SOURCE_LABELS.get(s, s)), "orders": n, "share": pct(n, total),
             "indirect": s in INDIRECT_SOURCES, "tier": _tier(s)}
            for s, n in sorted(counts.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99) if n]


def _tier(source: str) -> str:
    if source == "other_order":
        return "inferred"
    if source == "email_domain":
        return "probable"
    return "unknown" if not source else "exact"


def _country_table(qs, field, n=10):
    rows = list(qs.values(field).annotate(n=Count("id")).order_by("-n"))
    known = sum(r["n"] for r in rows) or 1
    return [{"code": r[field], "name": country_name(r[field]), "flag": country_flag(r[field]), "n": r["n"],
             "share": pct(r["n"], known)} for r in rows[:n]]


def _build(scope: ReportScope) -> Dict:
    orders = scope.orders
    total = orders.count()
    out = {"total_orders": total}
    if not total:
        return out

    # ── Countries ─────────────────────────────────────────────────────────────
    mode = scope.params.get("countries", "exact")
    mode = mode if mode in COUNTRY_MODES else "exact"
    out["country_mode"] = mode
    extra = {"exact": [], "inferred": ["other_order"], "probable": ["other_order", "email_domain"]}[mode]
    whens = [When(~Q(country_code=""), then=F("country_code"))]
    if extra:
        whens.append(When(country_inferred_source__in=extra, then=F("country_inferred")))
    orders_c = orders.annotate(country_shown=Case(*whens, default=Value(""), output_field=CharField()))
    rows = list(orders_c.values("country_shown").annotate(
        orders=Count("id"), tickets=Sum("ticket_count"), revenue=Sum("total_gross"),
        returning=Count("id", filter=Q(is_repeat_buyer=True)),
        local=Count("id", filter=Q(is_local_buyer=True)),
    ))
    for r in rows:
        r["country_code"] = r.pop("country_shown")
    table = []
    for r in sorted(rows, key=lambda r: -r["orders"]):
        table.append({
            "code": r["country_code"] or "", "name": country_name(r["country_code"]),
            "flag": country_flag(r["country_code"]), "orders": r["orders"], "tickets": r["tickets"] or 0,
            "revenue": float(r["revenue"] or 0), "aov": float(r["revenue"] or 0) / r["orders"],
            "returning_pct": pct(r["returning"], r["orders"]), "share": pct(r["orders"], total),
        })
    folded = fold_other([dict(t) for t in table], "name", "orders", n=10, other_label=_("Other"))
    out["countries"] = table
    out["country_chart"] = spec("bar", [r["name"] for r in folded], [serie(_("Orders"), [r["orders"] for r in folded])],
                                horizontal=True, y_title=_("Orders"))
    out["country_sources"] = _country_sources(orders, total)
    out["travel"] = _country_table(orders.exclude(travel_country_code=""), "travel_country_code")
    out["travel_coverage"] = pct(sum(r["n"] for r in out["travel"]), total)
    docs = scope.admissions.exclude(document_country="")
    out["documents"] = _country_table(docs, "document_country")
    out["document_coverage"] = pct(sum(r["n"] for r in out["documents"]), scope.admissions.count())
    local = sum(r["local"] for r in rows)
    out["local_pct"] = pct(local, total) if scope.config and scope.config.home_country else None

    # ── Cities (free text from invoice addresses, normalised) ───────────────
    from django.db.models.functions import Lower, Trim
    city_rows = list(orders.exclude(city="").annotate(c=Lower(Trim("city"))).values("country_code", "c").annotate(
        orders=Count("id"), tickets=Sum("ticket_count"), revenue=Sum("total_gross"),
        returning=Count("id", filter=Q(is_repeat_buyer=True)),
    ).order_by("-orders")[:15])
    with_city = orders.exclude(city="").count()
    out["cities"] = [{
        "name": r["c"].title(), "flag": country_flag(r["country_code"]), "orders": r["orders"],
        "tickets": r["tickets"] or 0, "revenue": float(r["revenue"] or 0),
        "returning_pct": pct(r["returning"], r["orders"]), "share": pct(r["orders"], with_city),
    } for r in city_rows]
    out["city_coverage"] = pct(with_city, total)

    # ── Age (per ticket where known, else per order) ─────────────────────────
    ages = dict(scope.admissions.exclude(age_range="").values_list("age_range").annotate(n=Count("id")))
    basis = _("tickets")
    if not ages:
        ages = dict(orders.exclude(age_range="").values_list("age_range").annotate(n=Count("id")))
        basis = _("orders")
    if ages:
        labels = [a for a in AGE_BUCKETS if a in ages]
        known = sum(ages.values())
        out["age_chart"] = spec("bar", [l.replace("-", "–") for l in labels],
                                [serie(_("Share"), [round(ages[a] / known * 100, 1) for a in labels])],
                                fmt="percent", y_title=_("Share of %(basis)s with a known age") % {"basis": basis})
        out["ages"] = [{"label": a.replace("-", "–"), "count": ages[a], "pct": round(ages[a] / known * 100, 1)}
                       for a in labels]
        out["age_known"] = known
        out["age_basis"] = basis
    confirmed = orders.filter(is_age_confirmed=True).count()
    out["age_confirmed"] = confirmed

    # ── Language & payment ────────────────────────────────────────────────────
    langs = list(orders.exclude(language="").values("language").annotate(n=Count("id")).order_by("-n"))
    langs = fold_other(langs, "language", "n", n=7, other_label=_("Other"))
    out["language_chart"] = spec("bar", [str(r["language"]).upper() if r["language"] != _("Other") else r["language"]
                                         for r in langs], [serie(_("Orders"), [r["n"] for r in langs])],
                                 horizontal=True)
    pays = list(orders.exclude(payment_provider="").values("payment_provider").annotate(
        n=Count("id"), revenue=Sum("total_gross"), aov=Avg("total_gross")).order_by("-n"))
    out["payments"] = [{"label": provider_label(r["payment_provider"]), "orders": r["n"],
                        "share": pct(r["n"], total), "revenue": float(r["revenue"] or 0),
                        "aov": float(r["aov"] or 0)} for r in pays]
    out["payment_chart"] = spec("bar", [p["label"] for p in out["payments"]],
                                [serie(_("Orders"), [p["orders"] for p in out["payments"]])], horizontal=True)

    # ── Personas by purchase timing ──────────────────────────────────────────
    out["personas"], out["persona_note"] = _personas(scope)

    # ── Group size ────────────────────────────────────────────────────────────
    sizes = dict(orders.values_list("ticket_count").annotate(n=Count("id")))
    labels = ["1", "2", "3", "4", "5+"]
    values = [sizes.get(1, 0), sizes.get(2, 0), sizes.get(3, 0), sizes.get(4, 0),
              sum(v for k, v in sizes.items() if k >= 5)]
    out["group_chart"] = spec("bar", labels, [serie(_("Orders"), values)], x_title=_("Tickets per order"),
                              y_title=_("Orders"))
    out["avg_group"] = round((orders.aggregate(a=Avg("ticket_count"))["a"] or 0), 2)

    # ── Caravan (only when the event collects it) ────────────────────────────
    caravan = orders.filter(has_caravan_pass=True)
    n_caravan = caravan.count()
    if n_caravan:
        vans = list(caravan.exclude(camper_van_length_bucket="").values("camper_van_length_bucket")
                    .annotate(n=Count("id")).order_by("camper_van_length_bucket"))
        out["caravan"] = {
            "count": n_caravan, "pct": pct(n_caravan, total),
            "chart": spec("bar", [v["camper_van_length_bucket"] for v in vans], [serie(_("Vans"), [v["n"] for v in vans])]),
        }
    return out


def _personas(scope: ReportScope):
    """
    Early bird / regular / last minute, with windows derived from each
    edition's own sales period: early bird = first quarter of the sales
    window (at least 30 days), last minute = the final 30 days.
    """
    starts = {r["event_id"]: r["first"] for r in scope.orders.values("event_id").annotate(first=Min("order_datetime"))}
    events = {e.pk: e for e, _c in scope.series_events}
    windows = {}
    for ev_id, first in starts.items():
        ev = events.get(ev_id) or scope.event
        if not ev.date_from or not first:
            continue
        window = max(1, (scope.local_date(ev.date_from) - scope.local_date(first)).days)
        early_days = max(30, window // 4)
        windows[ev_id] = window - early_days  # orders with more days left than this are early birds

    keys = [_("Early bird"), _("Regular"), _("Last minute (final %(d)s days)") % {"d": LAST_MINUTE_DAYS}]
    acc = {k: {"orders": 0, "revenue": 0.0, "returning": 0, "addons": 0, "tickets": 0} for k in keys}
    for r in scope.orders.exclude(days_before_event__isnull=True).values(
            "event_id", "days_before_event", "total_gross", "is_repeat_buyer", "has_addons", "ticket_count"
    ).iterator(chunk_size=2000):
        cutoff = windows.get(r["event_id"], 180)
        d = r["days_before_event"]
        k = keys[0] if d > cutoff else keys[2] if d <= LAST_MINUTE_DAYS else keys[1]
        a = acc[k]
        a["orders"] += 1
        a["tickets"] += r["ticket_count"]
        a["revenue"] += float(r["total_gross"] or 0)
        a["returning"] += 1 if r["is_repeat_buyer"] else 0
        a["addons"] += 1 if r["has_addons"] else 0
    rows = []
    for k in keys:
        a = acc[k]
        rows.append({"label": k, **a, "aov": a["revenue"] / a["orders"] if a["orders"] else 0,
                     "returning_pct": pct(a["returning"], a["orders"]), "addon_pct": pct(a["addons"], a["orders"])})
    cur = windows.get(scope.event.pk)
    start = starts.get(scope.event.pk)
    note = ""
    if cur is not None and start:
        window = (scope.local_date(scope.event.date_from) - scope.local_date(start)).days
        note = _("Early bird: first %(e)s days of sales · Last minute: final %(l)s days") % {
            "e": max(1, window - cur), "l": LAST_MINUTE_DAYS}
    return rows, note
