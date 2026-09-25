"""
Tickets section: products, categories, variations, price points, add-ons
and vouchers.
"""
from collections import defaultdict
from typing import Dict

from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.utils.translation import gettext as _

from .charts import fold_other, serie, spec
from .scope import ReportScope, pct


def build(scope: ReportScope) -> Dict:
    return scope.cached("tickets", lambda: _build(scope))


def _build(scope: ReportScope) -> Dict:
    adm = scope.admissions
    n_adm = adm.count()
    n_orders = scope.orders.count()
    out = {"admissions": n_adm, "orders": n_orders}
    if not n_adm:
        return out
    any_checkin = adm.filter(checked_in=True).exists()

    products = list(adm.values("item_name").annotate(
        tickets=Count("id"), revenue=Sum("price"), avg=Avg("price"),
        identified=Count("id", filter=~Q(attendee_person_key="")),
        returning=Count("id", filter=Q(is_returning_attendee=True)),
        checked=Count("id", filter=Q(checked_in=True)),
    ).order_by("-tickets"))
    out["products"] = [{
        "name": p["item_name"], "tickets": p["tickets"], "revenue": float(p["revenue"] or 0),
        "avg": float(p["avg"] or 0), "share": pct(p["tickets"], n_adm),
        "returning_pct": pct(p["returning"], p["identified"]) if p["identified"] else None,
        "checkin_pct": pct(p["checked"], p["tickets"]) if any_checkin else None,
    } for p in products]
    folded = fold_other([dict(p) for p in out["products"]], "name", "tickets", n=10, other_label=_("Other"))
    out["product_chart"] = spec("bar", [p["name"] for p in folded], [serie(_("Tickets"), [p["tickets"] for p in folded])],
                                horizontal=True)

    categories = list(scope.tickets.values("item_category").annotate(
        tickets=Count("id", filter=Q(is_addon=False)), items=Count("id"), revenue=Sum("price"),
    ).order_by("-revenue"))
    if len(categories) > 1 or (categories and categories[0]["item_category"]):
        out["categories"] = [{"name": c["item_category"] or _("Uncategorised"), "tickets": c["tickets"],
                              "items": c["items"], "revenue": float(c["revenue"] or 0)} for c in categories]

    variations = list(scope.tickets.exclude(variation_name="").values("item_name", "variation_name").annotate(
        n=Count("id"), revenue=Sum("price"), avg=Avg("price")).order_by("item_name", "-n"))
    out["variations"] = [{"product": v["item_name"], "name": v["variation_name"], "count": v["n"],
                          "revenue": float(v["revenue"] or 0), "avg": float(v["avg"] or 0)} for v in variations]

    # Price points: each distinct price a product sold at, with its sales window.
    # Voucher sales are excluded (they have their own table) and variations
    # are listed separately, so every row is a real list price.
    points = list(scope.tickets.filter(voucher_code="").values("item_name", "variation_name", "is_addon", "price").annotate(
        n=Count("id"), first=Min("order_fact__order_datetime"), last=Max("order_fact__order_datetime"),
    ).order_by("is_addon", "item_name", "variation_name", "first"))
    grouped = defaultdict(list)
    for p in points:
        name = f"{p['item_name']} ({p['variation_name']})" if p["variation_name"] else p["item_name"]
        grouped[name].append(p)
    out["price_points"] = [{
        "product": name, "addon": rows[0]["is_addon"],
        "points": [{"price": float(r["price"]), "count": r["n"], "first": scope.local(r["first"]),
                    "last": scope.local(r["last"])} for r in rows],
    } for name, rows in grouped.items() if len(rows) > 1]

    # Add-ons
    addon_rows = list(scope.tickets.filter(is_addon=True).values("item_name").annotate(
        count=Count("id"), orders=Count("order_fact_id", distinct=True), revenue=Sum("price"),
    ).order_by("-count"))
    out["addons"] = [{"name": a["item_name"], "count": a["count"], "revenue": float(a["revenue"] or 0),
                      "attach_pct": pct(a["orders"], n_orders)} for a in addon_rows]
    if addon_rows:
        seg = scope.orders.aggregate(
            ret=Count("id", filter=Q(is_repeat_buyer=True)),
            ret_add=Count("id", filter=Q(is_repeat_buyer=True, has_addons=True)),
            new=Count("id", filter=Q(is_repeat_buyer=False)),
            new_add=Count("id", filter=Q(is_repeat_buyer=False, has_addons=True)),
        )
        segs = [
            {"label": _("Returning buyers"), "total": seg["ret"], "with": seg["ret_add"],
             "pct": pct(seg["ret_add"], seg["ret"])},
            {"label": _("First-time buyers"), "total": seg["new"], "with": seg["new_add"],
             "pct": pct(seg["new_add"], seg["new"])},
        ]
        for r in scope.orders.exclude(age_range="").values("age_range").annotate(
                total=Count("id"), w=Count("id", filter=Q(has_addons=True))).order_by("age_range"):
            segs.append({"label": _("Age %(a)s") % {"a": r["age_range"]}, "total": r["total"], "with": r["w"],
                         "pct": pct(r["w"], r["total"])})
        out["addon_segments"] = segs

    if not scope.merged:
        out["capacity"] = _capacity(scope.event)

    # Vouchers
    vouchers = list(scope.tickets.exclude(voucher_code="").values("voucher_tag").annotate(
        tickets=Count("id"), orders=Count("order_fact_id", distinct=True), codes=Count("voucher_code", distinct=True),
        revenue=Sum("price"), avg=Avg("price"),
    ).order_by("-tickets"))
    if vouchers:
        paid_avg = adm.filter(voucher_code="").aggregate(a=Avg("price"))["a"]
        out["vouchers"] = [{
            "tag": v["voucher_tag"] or _("(no tag)"), "tickets": v["tickets"], "orders": v["orders"],
            "codes": v["codes"], "revenue": float(v["revenue"] or 0), "avg": float(v["avg"] or 0),
        } for v in vouchers]
        out["voucher_share"] = pct(sum(v["tickets"] for v in vouchers), scope.tickets.count())
        out["no_voucher_avg"] = float(paid_avg or 0)
    return out


def _capacity(event):
    """
    Quota usage straight from Pretix's own availability engine (read-only):
    how much of each quota is paid, pending, and still available.
    """
    from pretix.base.services.quotas import QuotaAvailability

    quotas = list(event.quotas.filter(subevent__isnull=True).prefetch_related("items").order_by("name"))
    quotas = [q for q in quotas if q.size]
    if not quotas:
        return []
    qa = QuotaAvailability(full_results=True, count_waitinglist=True)
    qa.queue(*quotas)
    qa.compute()
    rows = []
    for q in quotas:
        paid = qa.count_paid_orders.get(q, 0)
        pending = qa.count_pending_orders.get(q, 0)
        state, available = qa.results.get(q, (None, None))
        rows.append({
            "name": q.name, "size": q.size, "paid": paid, "pending": pending,
            "available": available if available is not None else max(q.size - paid - pending, 0),
            "waiting": qa.count_waitinglist.get(q, 0),
            "sold_pct": pct(paid, q.size),
            "reserved_pct": pct(paid + pending, q.size),
            "products": ", ".join(str(i.name) for i in q.items.all()),
        })
    return rows
