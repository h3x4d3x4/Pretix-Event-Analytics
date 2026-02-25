"""
Shared resync logic — used by both the management command and the Celery
UI-triggered task so there is only one code path to maintain.

  resync_event(event, include_checkin=False, log_fn=None)
      → {'processed': int, 'skipped': int}

log_fn(message: str) is an optional callback for progress reporting.
If None, progress is silently discarded.
"""
import logging
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


def resync_event(
    event,
    include_checkin: bool = False,
    log_fn: Optional[Callable[[str], None]] = None,
) -> Dict[str, int]:
    """
    Delete and rebuild all analytics facts for a single event.

    :param event: Pretix Event instance.
    :param include_checkin: If True, populate checkin_completed from Pretix
                            check-in records and use it in the probability score.
                            Run this after the event has physically ended.
    :param log_fn: Optional callable for progress messages.
    :returns: {'processed': n, 'skipped': m}
    """
    from pretix.base.models import Order

    from ..models import (
        AnalyticsOrderFact,
        AnalyticsTicketFact,
        EventAnalyticsConfig,
        AnalyticsIdentity,
    )
    from .cohort_service import invalidate_cohort_cache
    from .normalizer import normalize_order
    from .predictor import calculate_repeat_probability
    from .repeat_detector import evaluate_repeat_status

    def log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    # ── Validate config ───────────────────────────────────────────────────────
    try:
        config = EventAnalyticsConfig.objects.select_related("series").get(event=event)
    except EventAnalyticsConfig.DoesNotExist:
        raise ValueError(
            f"Event '{event.slug}' has no analytics config. "
            "Configure it first at /control/event/<org>/<event>/analytics/config/"
        )

    # ── Clear existing facts ──────────────────────────────────────────────────
    deleted_facts, _ = AnalyticsOrderFact.objects.filter(event=event).delete()
    log(f"Deleted {deleted_facts} existing order facts.")

    # ── Load checkin data (optional) ──────────────────────────────────────────
    from django_scopes import scope

    checkin_order_codes: set = set()
    if include_checkin:
        try:
            from pretix.base.models import Checkin
            with scope(organizer=event.organizer):
                checkin_order_codes = set(
                    Checkin.objects.filter(position__order__event=event)
                    .values_list("position__order__code", flat=True)
                    .distinct()
                )
            log(f"Found {len(checkin_order_codes)} orders with check-ins.")
        except ImportError:
            log("Warning: Checkin model not available, skipping checkin data.")

    # ── Fetch all paid orders ─────────────────────────────────────────────────
    with scope(organizer=event.organizer):
        orders = list(
            Order.objects.filter(event=event, status=Order.STATUS_PAID)
            .select_related("event__organizer", "invoice_address")
            .prefetch_related(
                "positions__item",
                "positions__variation",
                "positions__answers__question",
                "payments",
                "refunds",
            )
        )

    processed = 0
    skipped = 0

    for order in orders:
        try:
            order_fact_data, ticket_facts_data = normalize_order(order, config)

            identities = order_fact_data.pop("_identities", [])
            repeat_hashes = order_fact_data.pop("_repeat_hashes", []) # Clean up legacy key
            
            if identities:
                repeat_status = evaluate_repeat_status(event, identities)
                order_fact_data.update(repeat_status)

            checkin_done = order.code in checkin_order_codes
            order_fact_data["checkin_completed"] = checkin_done

            probability = calculate_repeat_probability(
                {
                    "repeat_count": order_fact_data.get("repeat_count", 0),
                    "ticket_count": order_fact_data.get("ticket_count", 1),
                    "is_group_order": order_fact_data.get("is_group_order", False),
                    "is_local_buyer": order_fact_data.pop("_is_local_buyer", False),
                    "bought_early": order_fact_data.pop("_bought_early", False),
                    "checkin_completed": checkin_done,
                }
            )
            order_fact_data["predicted_repeat_probability"] = probability
            order_fact_data.pop("_bought_early", None)
            order_fact_data.pop("_is_local_buyer", None)

            fact = AnalyticsOrderFact.objects.create(event=event, **order_fact_data)
            
            AnalyticsTicketFact.objects.bulk_create(
                [
                    AnalyticsTicketFact(order_fact=fact, event=event, **tf)
                    for tf in ticket_facts_data
                ]
            )
            
            AnalyticsIdentity.objects.bulk_create(
                [
                    AnalyticsIdentity(
                        order_fact=fact, 
                        event=event, 
                        identity_type=i["type"], 
                        identity_hash=i["hash"]
                    )
                    for i in identities
                ]
            )
            
            processed += 1

        except Exception as exc:
            logger.warning(
                "analytics resync: skipped order %s — %s", order.code, exc
            )
            skipped += 1

    # ── Invalidate caches for this series ─────────────────────────────────────
    if config.series:
        invalidate_cohort_cache(config.series.slug, event.organizer_id)

    log(f"Done: {processed} processed, {skipped} skipped.")
    return {"processed": processed, "skipped": skipped}
