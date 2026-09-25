"""
Resale: tickets that changed hands, across every channel.

Two channels are combined into one number — tickets whose holder changed —
counted once per ticket even when both channels touched it:

TicketSwap
    Read-only, from whichever record the installed TicketSwap plugin keeps:

    * 1.x: its own swap table (``TicketSwapSwap``: one row per swap, with
      ``success`` and ``created``) — only the position, date and success flag
      are read, never the stored names or e-mails;
    * 2.x: counters on the ticket (``OrderPosition.meta_info["ticketswap"]``,
      ``swap_count``), which carry no swap date.

    This plugin never writes to TicketSwap's data.

Manual name change
    Name edits in Pretix (by the buyer, the ticket holder or an admin) from
    Pretix's audit log. Pretix logs only the *new* values, so a change counts
    as a new holder only with evidence:

    * the birth date or attendee e-mail changed in the same edit, or
    * the name differs clearly from the previously logged name (a second
      rename; typo fixes and added middle names are ignored).

    Other name edits are reported as "unclear" and not counted — when in
    doubt, a ticket is not called resold.

READ-ONLY AGAINST PRETIX CORE.
"""
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

TICKETSWAP_PLUGIN = "pretix_ticketswap"


def _is_real_name_change(old_name: str, new_name: str, threshold: float = 0.65) -> bool:
    """
    True only for a substantial name change, not a correction:
      "John Doe" → "Jane Smith"  real change
      "John Doe" → "John Doa"    typo fix (similar)
      "John Doe" → "John M Doe"  middle name added (substring)
    """
    old_c = " ".join(old_name.lower().split())
    new_c = " ".join(new_name.lower().split())
    if not old_c or not new_c or old_c == new_c:
        return False
    if old_c in new_c or new_c in old_c:
        return False
    return SequenceMatcher(None, old_c, new_c).ratio() < threshold


def _flatten(parts) -> str:
    if isinstance(parts, dict):
        return " ".join(v for k, v in parts.items()
                        if not k.startswith("_") and isinstance(v, str) and v.strip()).strip()
    return str(parts).strip() if parts else ""


def _load(data) -> dict:
    if isinstance(data, dict):
        return data
    try:
        out = json.loads(data or "{}")
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


@dataclass
class TicketChange:
    """What happened to one ticket (``slot``: a position id, or order + form index)."""
    order_code: str
    position_id: Optional[int] = None
    swaps: int = 0
    personalized: bool = False
    manual: int = 0            # name edits that show a new holder
    unclear: int = 0           # name edits without evidence either way
    last_manual: object = None
    last_swap: object = None
    names: List[str] = field(default_factory=list)

    @property
    def changed_hands(self) -> bool:
        return self.swaps > 0 or self.manual > 0

    @property
    def channel(self) -> str:
        if self.swaps and self.manual:
            return "both"
        if self.swaps:
            return "ticketswap"
        if self.manual:
            return "manual"
        return ""


def ticketswap_active(event) -> bool:
    return TICKETSWAP_PLUGIN in (event.plugins or "").split(",")


def _swap_model():
    """TicketSwap 1.x's swap table, or None when that plugin/version is not installed."""
    from django.apps import apps

    try:
        return apps.get_model(TICKETSWAP_PLUGIN, "TicketSwapSwap")
    except (LookupError, ValueError):
        return None


def _swap_rows(event):
    """(position id, created) for every successful TicketSwap 1.x swap of ``event``."""
    model = _swap_model()
    if model is None:
        return []
    try:
        return list(model.objects.filter(event=event, success=True).values_list("order_position_id", "created"))
    except Exception:
        logger.exception("analytics: could not read TicketSwap swaps for %s", event.slug)
        return []


def collect(event) -> Dict[str, TicketChange]:
    """Every ticket of ``event`` with a swap or a name edit, keyed by slot."""
    from django.contrib.contenttypes.models import ContentType
    from django_scopes import scopes_disabled
    from pretix.base.models import LogEntry, Order, OrderPosition

    from .age_bucketer import _is_birth_question

    tickets: Dict[str, TicketChange] = {}
    with scopes_disabled():
        admissions = defaultdict(list)
        codes = {}
        for pid, oid, code in OrderPosition.objects.filter(
                order__event=event, addon_to__isnull=True, item__admission=True,
        ).order_by("positionid").values_list("pk", "order_id", "order__code"):
            admissions[oid].append(pid)
            codes[oid] = code

        # ── TicketSwap 1.x: swap table (read-only; only ids, dates, success) ──
        # Both channels count admission tickets only (same set as the "of N tickets" total).
        admission_code = {pid: codes[oid] for oid, pids in admissions.items() for pid in pids}
        for pid, created in _swap_rows(event):
            if pid not in admission_code:
                continue  # add-on or non-admission product
            t = tickets.get(f"p{pid}")
            if t is None:
                t = tickets[f"p{pid}"] = TicketChange(order_code=admission_code[pid], position_id=pid)
            t.swaps += 1
            if created and (t.last_swap is None or created > t.last_swap):
                t.last_swap = created

        # ── TicketSwap 2.x: counters on the ticket (read-only) ────────────────
        for pid, code, meta in OrderPosition.all.filter(
                order__event=event, meta_info__contains='"ticketswap"',
        ).values_list("pk", "order__code", "meta_info"):
            ts = _load(meta).get("ticketswap") or {}
            try:
                swaps = int(ts.get("swap_count") or 0)
            except (TypeError, ValueError):
                swaps = 0
            if swaps and pid in admission_code:
                t = tickets.setdefault(f"p{pid}", TicketChange(order_code=code, position_id=pid))
                t.swaps = max(t.swaps, swaps)
                t.personalized = bool(ts.get("personalized"))

        # ── Manual name changes (audit log) ────────────────────────────────
        birth_keys = {f"question_{q.pk}" for q in event.questions.all() if _is_birth_question(str(q.question))}
        order_ct = ContentType.objects.get_for_model(Order)
        logs = LogEntry.objects.filter(
            event=event, action_type="pretix.event.order.modified", content_type=order_ct,
            data__contains="attendee_name_parts",
        ).values_list("object_id", "datetime", "data").order_by("datetime", "pk")
        for oid, dt, data in logs.iterator(chunk_size=500):
            items = _load(data).get("data")
            if not isinstance(items, list):
                continue
            for index, item in enumerate(items):
                if not isinstance(item, dict) or "attendee_name_parts" not in item:
                    continue
                pid = item.get("position")
                if not pid and len(admissions.get(oid, [])) == 1:
                    pid = admissions[oid][0]
                slot = f"p{pid}" if pid else f"o{oid}:{index}"
                t = tickets.setdefault(slot, TicketChange(order_code=codes.get(oid, ""), position_id=pid))
                if not t.order_code:
                    t.order_code = codes.get(oid, "")
                new_name = _flatten(item.get("attendee_name_parts"))
                evidence = "attendee_email" in item or bool(birth_keys & set(item))
                known_before = bool(t.names and new_name)
                if evidence or (known_before and _is_real_name_change(t.names[-1], new_name)):
                    t.manual += 1
                    t.last_manual = dt
                elif not known_before:
                    t.unclear += 1   # previous name not logged: cannot tell
                # else: a correction of the previously logged name
                if new_name:
                    t.names.append(new_name)
    for t in tickets.values():
        t.names = []  # never keep names beyond this function
    return {k: t for k, t in tickets.items() if t.changed_hands or t.unclear}


def resale_stats(event) -> Dict:
    from ..models import AnalyticsTicketFact

    changes = collect(event)
    sold = AnalyticsTicketFact.objects.filter(
        event=event, is_addon=False, order_fact__order_status="p", order_fact__is_refunded=False,
    )
    from django_scopes import scopes_disabled
    from pretix.base.models import OrderPosition

    with scopes_disabled():
        admission = set(OrderPosition.all.filter(
            order__event=event, addon_to__isnull=True, item__admission=True,
        ).values_list("pk", flat=True))
    # Same scope as both channels: admission tickets of paid, non-refunded orders.
    facts = {pid: (item, ret, key) for pid, item, ret, key in sold.values_list(
        "position_id", "item_name", "is_returning_attendee", "attendee_person_key") if pid in admission}
    total = len(facts)
    paid_codes = set(sold.values_list("order_fact__order_code", flat=True))
    # Only tickets that are still sold (not canceled or refunded) count.
    changes = {k: t for k, t in changes.items()
               if ((t.position_id in facts) if t.position_id else (t.order_code in paid_codes))}
    changed = [t for t in changes.values() if t.changed_hands]

    channels = {"ticketswap": 0, "manual": 0, "both": 0}
    for t in changed:
        channels[t.channel] += 1

    by_product = defaultdict(lambda: [0, 0])
    for item, _r, _k in facts.values():
        by_product[item][0] += 1
    holders = {"returning": 0, "first_time": 0, "unknown": 0}
    by_month = defaultdict(lambda: {"ticketswap": 0, "manual": 0})
    for t in changed:
        fact = facts.get(t.position_id)
        if fact:
            by_product[fact[0]][1] += 1
            holders["unknown" if not fact[2] else ("returning" if fact[1] else "first_time")] += 1
        else:
            holders["unknown"] += 1
        if t.swaps and t.last_swap:
            by_month[t.last_swap.strftime("%Y-%m")]["ticketswap"] += 1
        if t.manual and t.last_manual:
            by_month[t.last_manual.strftime("%Y-%m")]["manual"] += 1

    def rate(n, d):
        return round(n / d * 100, 1) if d else 0.0

    return {
        "tickets": total,
        "changed": len(changed),
        "rate": rate(len(changed), total),
        "channels": channels,
        "ticketswap_active": ticketswap_active(event),
        "chains": sum(1 for t in changed if t.swaps >= 2),
        "personalized": sum(1 for t in changed if t.swaps and t.personalized),
        "unclear": sum(1 for t in changes.values() if t.unclear and not t.changed_hands),
        "holders": holders,
        "by_product": sorted(
            ({"label": k, "tickets": v[0], "changed": v[1], "rate": rate(v[1], v[0])}
             for k, v in by_product.items() if v[1]), key=lambda r: -r["changed"]),
        "by_month": [{"month": m, **n} for m, n in sorted(by_month.items())],
        "swaps_dated": any(t.last_swap for t in changed),
        "rows": sorted(changed, key=lambda t: (t.order_code, t.position_id or 0)),
    }


def resale_by_edition(series, organizer) -> List[Dict]:
    """Changed-hands rate per active edition of a series."""
    from django_scopes import scopes_disabled
    from pretix.base.models import Event

    with scopes_disabled():
        events = list(Event.objects.filter(
            organizer=organizer, analytics_config__series=series, analytics_config__is_active=True,
        ).select_related("analytics_config").order_by("analytics_config__edition_year"))
    out = []
    for evt in events:
        s = resale_stats(evt)
        if s["tickets"]:
            out.append({"edition_year": evt.analytics_config.edition_year, "rate": s["rate"],
                        "changed": s["changed"], "tickets": s["tickets"], "channels": s["channels"]})
    return out
