"""
Shared resync logic — used by both the management command and the Celery
UI-triggered task so there is only one code path to maintain.

  resync_event(event, include_checkin=False, log_fn=None)
      → {'processed': int, 'skipped': int}

log_fn(message: str) is an optional callback for progress reporting.
If None, progress is silently discarded.

READ-ONLY AGAINST PRETIX CORE
-----------------------------
This module reads Pretix orders, positions, answers, payments, refunds
and check-ins — it never writes to them. All writes go to the plugin's
own analytics tables.

CONCURRENCY
-----------
During resync we delete every AnalyticsOrderFact row for the event and
rebuild. If a new paid order arrives mid-rebuild, its ingestion task
would otherwise query an empty analytics table and wrongly mark the
buyer as non-repeat. We publish a shared cache flag (``resync_lock_key``)
while a resync is in flight; ``tasks.process_order_paid`` checks it and
requeues itself until the flag clears. Resync itself runs its per-order
update inside ``transaction.atomic()`` so concurrent repeat-detection
reads are serialised.
"""
import logging
from typing import Callable, Dict, Optional

from django.core.cache import cache

logger = logging.getLogger(__name__)

# Shared between resync_service and signal-triggered tasks. Held for the
# duration of a resync; any order_paid task that sees this key requeues.
RESYNC_LOCK_TTL_SECONDS = 30 * 60  # upper bound; resync clears it on exit


def resync_lock_key(event_pk: int) -> str:
    return f"pretix_analytics:resync_in_progress:{event_pk}"


def is_resync_in_progress(event_pk: int) -> bool:
    return bool(cache.get(resync_lock_key(event_pk)))


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
    from django.db import transaction
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

    # ── Publish resync-in-progress flag ───────────────────────────────────────
    # Signal-triggered order_paid tasks check this and requeue themselves
    # so they don't race against the table rebuild.
    lock_key = resync_lock_key(event.pk)
    cache.set(lock_key, True, timeout=RESYNC_LOCK_TTL_SECONDS)
    try:
        return _resync_event_unlocked(
            event, config, include_checkin, log,
            Order=Order,
            AnalyticsOrderFact=AnalyticsOrderFact,
            AnalyticsTicketFact=AnalyticsTicketFact,
            AnalyticsIdentity=AnalyticsIdentity,
            normalize_order=normalize_order,
            evaluate_repeat_status=evaluate_repeat_status,
            calculate_repeat_probability=calculate_repeat_probability,
            invalidate_cohort_cache=invalidate_cohort_cache,
            transaction=transaction,
        )
    finally:
        cache.delete(lock_key)


def _resync_event_unlocked(
    event, config, include_checkin, log,
    *, Order, AnalyticsOrderFact, AnalyticsTicketFact, AnalyticsIdentity,
    normalize_order, evaluate_repeat_status, calculate_repeat_probability,
    invalidate_cohort_cache, transaction,
):
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

    # ── Fetch paid order PKs, then process in chunks to avoid OOM ───────────
    with scope(organizer=event.organizer):
        order_pks = list(
            Order.objects.filter(event=event, status=Order.STATUS_PAID)
            .order_by("pk")
            .values_list("pk", flat=True)
        )

    CHUNK_SIZE = 200
    processed = 0
    skipped = 0

    for i in range(0, len(order_pks), CHUNK_SIZE):
        chunk_pks = order_pks[i:i + CHUNK_SIZE]
        with scope(organizer=event.organizer):
            chunk_orders = list(
                Order.objects.filter(pk__in=chunk_pks)
                .select_related("event__organizer", "invoice_address")
                .prefetch_related(
                    "positions__item",
                    "positions__variation",
                    "positions__answers__question",
                    "payments",
                    "refunds",
                )
            )
        for order in chunk_orders:
            try:
                order_fact_data, ticket_facts_data = normalize_order(order, config)

                identities = order_fact_data.pop("_identities", [])
                order_fact_data.pop("_repeat_hashes", None)

                checkin_done = order.code in checkin_order_codes
                order_fact_data["checkin_completed"] = checkin_done
                is_local_buyer = order_fact_data.pop("_is_local_buyer", False)
                bought_early = order_fact_data.pop("_bought_early", False)

                # Serialise repeat-detection + insert. Two concurrent writers
                # (resync and a signal-triggered task) would otherwise both
                # see "no prior edition" and mark each other as non-repeat.
                with transaction.atomic():
                    if identities:
                        repeat_status = evaluate_repeat_status(event, identities)
                        order_fact_data.update(repeat_status)

                    probability = calculate_repeat_probability(
                        {
                            "repeat_count": order_fact_data.get("repeat_count", 0),
                            "ticket_count": order_fact_data.get("ticket_count", 1),
                            "is_group_order": order_fact_data.get("is_group_order", False),
                            "is_local_buyer": is_local_buyer,
                            "bought_early": bought_early,
                            "checkin_completed": checkin_done,
                        }
                    )
                    order_fact_data["predicted_repeat_probability"] = probability

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
                                identity_hash=i["hash"],
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
