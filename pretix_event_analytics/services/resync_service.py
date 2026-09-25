"""
Shared resync logic — used by both the management command and the Celery
UI-triggered task so there is only one code path to maintain.

  resync_event(event, include_checkin=True, log_fn=None)
      → {'processed': int, 'skipped': int, 'removed': int}

log_fn(message: str) is an optional callback for progress reporting.

READ-ONLY AGAINST PRETIX CORE
-----------------------------
This module reads Pretix orders, positions, answers, payments, refunds
and check-ins — it never writes to them. All writes go to the plugin's
own analytics tables.

STRATEGY
--------
Resync *upserts* every ingestible order (paid, or canceled after payment)
through the same ``ingest.write_order`` used by live ingestion, then removes
facts whose order no longer qualifies. The table is never emptied, so the
dashboard keeps working during a resync and a failure halfway leaves the
previous data in place. Finally the series-wide identity resolver rewrites
repeat/person fields for every edition, which makes the result independent
of the order in which editions are synced.

CONCURRENCY
-----------
A shared cache flag (``resync_lock_key``) is published while a resync runs;
live ingestion tasks see it and requeue themselves until it clears.
"""
import logging
from typing import Callable, Dict, List, Optional

from django.core.cache import cache

logger = logging.getLogger(__name__)

# Upper bound; resync clears the flag on exit.
RESYNC_LOCK_TTL_SECONDS = 30 * 60
CHUNK_SIZE = 200


def resync_lock_key(event_pk: int) -> str:
    return f"pretix_analytics:resync_in_progress:{event_pk}"


def is_resync_in_progress(event_pk: int) -> bool:
    return bool(cache.get(resync_lock_key(event_pk)))


def _load_order_pks(event) -> List[int]:
    """PKs of every order we keep facts for (paid, or canceled after payment)."""
    from django.db.models import Q
    from django_scopes import scope
    from pretix.base.models import Order

    with scope(organizer=event.organizer):
        return list(
            Order.objects.filter(event=event)
            .filter(
                Q(status=Order.STATUS_PAID)
                | Q(status=Order.STATUS_CANCELED, payments__state__in=("confirmed", "refunded"))
                | Q(status=Order.STATUS_CANCELED, refunds__state__in=("done", "transit"))
            )
            .order_by("pk")
            .values_list("pk", flat=True)
            .distinct()
        )


def resync_event(
    event,
    include_checkin: bool = True,
    log_fn: Optional[Callable[[str], None]] = None,
    resolve_people: bool = True,
) -> Dict[str, int]:
    """
    Rebuild all analytics facts for a single event.

    :param include_checkin: kept for backwards compatibility; check-ins are
                            now always read (they are simply empty before
                            the event).
    :param resolve_people: run the series identity resolver afterwards.
                           Callers resyncing a whole series pass False and
                           resolve once at the end.
    """
    from ..models import EventAnalyticsConfig

    def log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    try:
        config = EventAnalyticsConfig.objects.select_related("series").get(event=event)
    except EventAnalyticsConfig.DoesNotExist:
        raise ValueError(
            f"Event '{event.slug}' has no analytics config. "
            "Configure it first at /control/event/<org>/<event>/analytics/config/"
        )

    lock_key = resync_lock_key(event.pk)
    cache.set(lock_key, True, timeout=RESYNC_LOCK_TTL_SECONDS)
    try:
        result = _resync(event, config, log)
    finally:
        cache.delete(lock_key)

    if resolve_people:
        from .people import recompute_for_event
        recompute_for_event(event)
        log("Resolved returning buyers across the series.")
    else:
        from .versioning import bump
        bump(event.organizer_id)
    return result


def _resync(event, config, log) -> Dict[str, int]:
    from ..models import AnalyticsOrderFact
    from .ingest import is_ingestible, load_checkins, load_orders, write_order

    order_pks = _load_order_pks(event)
    log(f"Found {len(order_pks)} paid or refunded orders.")

    processed = skipped = 0
    kept_codes = set()
    for i in range(0, len(order_pks), CHUNK_SIZE):
        chunk = order_pks[i:i + CHUNK_SIZE]
        orders = load_orders(chunk)
        checkins = load_checkins(chunk)
        for order in orders:
            if not is_ingestible(order):
                continue
            try:
                write_order(order, config, checkins=checkins, quick_repeat=False)
                kept_codes.add(order.code)
                processed += 1
            except Exception as exc:
                logger.warning("analytics resync: skipped order %s — %s", order.code, exc)
                skipped += 1
        log(f"… {min(i + CHUNK_SIZE, len(order_pks))}/{len(order_pks)}")

    # Remove facts for orders that no longer qualify (deleted, or reverted
    # to pending). Skipped orders keep their previous fact, if any.
    stale = AnalyticsOrderFact.objects.filter(event=event).exclude(order_code__in=kept_codes)
    if skipped:
        stale = stale.none()
    removed = stale.count()
    stale.delete()

    log(f"Done: {processed} processed, {skipped} skipped, {removed} stale rows removed.")
    return {"processed": processed, "skipped": skipped, "removed": removed}


def resync_series(series, log_fn: Optional[Callable[[str], None]] = None) -> Dict[str, int]:
    """Resync every edition of a series, then resolve people once."""
    from ..models import EventAnalyticsConfig
    from .people import recompute_series

    totals = {"processed": 0, "skipped": 0, "removed": 0}
    configs = EventAnalyticsConfig.objects.filter(series=series).select_related("event__organizer")
    for cfg in configs:
        r = resync_event(cfg.event, log_fn=log_fn, resolve_people=False)
        for k in totals:
            totals[k] += r[k]
    recompute_series(series.organizer_id, series.slug)
    return totals
