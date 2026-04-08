"""
Secondary market tracking — detects ticket resale/scalping via name changes.

Uses Pretix's LogEntry model to find `pretix.event.order.modified` entries
where `attendee_name_parts` was changed.  A high rate of name changes suggests
tickets are being resold on the secondary market.

Only "real" name changes are counted — typo corrections and middle-name
additions are filtered out by comparing string similarity between old and new
name values extracted from the log entry JSON.
"""
import json
import logging
from difflib import SequenceMatcher
from typing import Dict, List

logger = logging.getLogger(__name__)


# ── Smart name change helpers ─────────────────────────────────────────────────

def _extract_names_from_log_data(data_input):
    """
    Try to extract (old_name, new_name) from a Pretix LogEntry.data value.
    Returns a (old_name, new_name) tuple or None if the format isn't recognised.

    Pretix stores order modification data in a few different formats depending
    on the version. We handle both JSONField (returns dict) and TextField (returns
    JSON string) transparently.
    """
    if isinstance(data_input, dict):
        data = data_input
    else:
        try:
            data = json.loads(data_input)
        except (json.JSONDecodeError, TypeError):
            return None

    if not isinstance(data, dict):
        return None

    def _flatten(parts) -> str:
        """Concatenate name_parts dict values, skipping _scheme."""
        if isinstance(parts, dict):
            return " ".join(
                v for k, v in parts.items()
                if k != "_scheme" and isinstance(v, str) and v.strip()
            ).strip()
        return str(parts).strip() if parts else ""

    # Format A: {"old": {"attendee_name_parts": {...}}, "new": {"attendee_name_parts": {...}}}
    old_blob = data.get("old")
    new_blob = data.get("new")
    if isinstance(old_blob, dict) and isinstance(new_blob, dict):
        old_parts = old_blob.get("attendee_name_parts")
        new_parts = new_blob.get("attendee_name_parts")
        if old_parts is not None and new_parts is not None:
            return _flatten(old_parts), _flatten(new_parts)

    # Format B: nested under "data" or "changes" key as a list of field changes
    changes = data.get("data") or data.get("changes") or []
    if isinstance(changes, list):
        for item in changes:
            if isinstance(item, dict) and item.get("field") == "attendee_name_parts":
                return _flatten(item.get("old", "")), _flatten(item.get("new", ""))

    return None


def _is_real_name_change(old_name: str, new_name: str, threshold: float = 0.65) -> bool:
    """
    Returns True only if this is a substantial name change, not a correction.

    Conservative heuristics applied in order:
    1. If either is empty or they're equal → not a change
    2. If one name is a substring of the other → just adding/removing a name
       component (e.g. adding a middle name) → not a real transfer
    3. If string similarity >= threshold → likely a typo correction → skip

    The default threshold of 0.65 means names must be <65% similar to count.
    Examples:
      "John Doe" → "Jane Smith"  ratio ≈ 0.47  → real change ✓
      "John Doe" → "John Doa"    ratio ≈ 0.94  → typo fix ✗
      "John Doe" → "John M Doe"  substring     → middle name added ✗
    """
    old_c = old_name.lower().strip()
    new_c = new_name.lower().strip()

    if not old_c or not new_c or old_c == new_c:
        return False

    # Substring check: adding/removing a name component
    if old_c in new_c or new_c in old_c:
        return False

    return SequenceMatcher(None, old_c, new_c).ratio() < threshold


# ── Public API ────────────────────────────────────────────────────────────────

def get_name_change_stats(event) -> Dict:
    """
    Analyse LogEntry records for attendee name changes on this event's orders.
    Only "real" name changes are counted (typo fixes and additions are excluded).

    :param event: Pretix Event instance.
    :returns: Dict with:
        - total_name_changes: int  (real changes only)
        - orders_with_changes: int
        - total_orders: int
        - name_change_rate: float (0-100)
        - changes_by_month: list of {month: str, count: int}
    """
    from django.contrib.contenttypes.models import ContentType
    from django_scopes import scope
    from pretix.base.models import LogEntry, Order

    result = {
        "total_name_changes": 0,
        "orders_with_changes": 0,
        "total_orders": 0,
        "name_change_rate": 0.0,
        "changes_by_month": [],
    }

    with scope(organizer=event.organizer):
        total_orders = Order.objects.filter(event=event, status=Order.STATUS_PAID).count()
        if not total_orders:
            # Fall back to analytics fact table — covers test data / development setups
            # where real Pretix Order objects were not created.
            from ..models import AnalyticsOrderFact
            total_orders = AnalyticsOrderFact.objects.filter(
                event=event, order_status="p", is_refunded=False
            ).count()
        result["total_orders"] = total_orders

        if not total_orders:
            return result

        order_ct = ContentType.objects.get_for_model(Order)

        # Fetch all candidate log entries (attendee_name_parts touched)
        candidates = list(
            LogEntry.objects.filter(
                event=event,
                action_type="pretix.event.order.modified",
                content_type=order_ct,
                data__contains="attendee_name_parts",
            )
            .values("id", "object_id", "datetime", "data")
            .order_by("datetime")
        )

    if not candidates:
        return result

    # Python-level filtering: only count real name changes
    real_order_ids: set = set()
    real_by_month: dict = {}
    real_count = 0

    for entry in candidates:
        names = _extract_names_from_log_data(entry.get("data") or "")
        if names is None:
            # Format not recognised — count conservatively as a real change
            is_real = True
        else:
            old_name, new_name = names
            is_real = _is_real_name_change(old_name, new_name)

        if is_real:
            real_count += 1
            real_order_ids.add(entry["object_id"])
            dt = entry.get("datetime")
            month_key = dt.strftime("%Y-%m") if dt else "unknown"
            real_by_month[month_key] = real_by_month.get(month_key, 0) + 1

    orders_with_changes = len(real_order_ids)

    result["total_name_changes"] = real_count
    result["orders_with_changes"] = orders_with_changes
    result["name_change_rate"] = (
        round(orders_with_changes / total_orders * 100, 1) if total_orders else 0.0
    )
    result["changes_by_month"] = [
        {"month": month, "count": count}
        for month, count in sorted(real_by_month.items())
    ]

    return result


def get_name_changes_by_edition(series, organizer) -> List[Dict]:
    """
    Returns name change stats per edition for cross-year comparison.
    Calls get_name_change_stats for each active edition in the series.

    :param series: EventSeries instance.
    :param organizer: Pretix Organizer instance.
    :returns: List of {edition_year, name_change_rate, orders_with_changes, total_orders}
              sorted by edition_year ascending.
    """
    from django_scopes import scope
    from pretix.base.models import Event

    with scope(organizer=organizer):
        events = list(
            Event.objects.filter(
                organizer=organizer,
                analytics_config__series=series,
                analytics_config__is_active=True,
            )
            .select_related("analytics_config")
            .order_by("analytics_config__edition_year")
        )

    out = []
    for evt in events:
        stats = get_name_change_stats(evt)
        if stats["total_orders"] == 0:
            continue
        out.append({
            "edition_year": evt.analytics_config.edition_year,
            "name_change_rate": stats["name_change_rate"],
            "orders_with_changes": stats["orders_with_changes"],
            "total_orders": stats["total_orders"],
        })

    return out
