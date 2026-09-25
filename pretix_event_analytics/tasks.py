"""
Celery tasks for asynchronous analytics processing.

Signal handlers must NOT do DB work inline — they queue these tasks instead.
This prevents request-cycle timeouts on large events (50k+ orders).

If Celery is not configured (single-server dev setups), Pretix falls back
to synchronous execution automatically.

READ-ONLY AGAINST PRETIX CORE
-----------------------------
Every query against pretix.base.models in this module must be read-only.
Writes, updates, and deletes are allowed ONLY against the plugin's own
models. This invariant is enforced statically by
``scripts/check_isolation.py``.
"""
import logging

from pretix.celery_app import app

logger = logging.getLogger(__name__)

# 60 s apart: covers any resync that keeps renewing its lock (see
# services.resync_service), with a hard stop after ~16 hours.
RESYNC_WAIT_RETRIES = 1000


def _config_for(event):
    from .models import EventAnalyticsConfig

    return EventAnalyticsConfig.objects.select_related("series").filter(event=event).first()


def _ingest(task, order_pk: int):
    """
    Shared body of the order lifecycle tasks: (re)write the facts for one
    order, or drop them when the order no longer qualifies.
    """
    from .models import AnalyticsOrderFact
    from .services.ingest import is_ingestible, load_orders, write_order
    from .services.people import schedule_recompute
    from .services.resync_service import is_resync_in_progress
    from .services.versioning import bump

    orders = load_orders([order_pk])
    if not orders:
        logger.info("analytics: order %s not found (may have been deleted), skipping.", order_pk)
        return
    order = orders[0]

    # Defer while a full resync is rebuilding this event's facts. The resync
    # keeps its lock alive per chunk, so wait as long as it holds it (the
    # lock expires on its own if the resync process dies).
    if is_resync_in_progress(order.event.pk):
        logger.info("analytics: resync in progress for %s — requeueing order %s.", order.event.slug, order_pk)
        raise task.retry(countdown=60, max_retries=RESYNC_WAIT_RETRIES)

    config = _config_for(order.event)
    if config is None:
        return  # plugin not configured for this event

    if not is_ingestible(order):
        deleted, _ = AnalyticsOrderFact.objects.filter(event=order.event, order_code=order.code).delete()
        if deleted:
            bump(order.event.organizer_id)
            schedule_recompute(order.event)
        return

    try:
        write_order(order, config)
    except ValueError as exc:
        logger.warning("analytics: skipping order %s (normalization rejected): %s", order_pk, exc)
        return
    except Exception as exc:
        logger.exception("analytics: ingestion failed for order %s", order_pk)
        raise task.retry(exc=exc, countdown=60 * (2 ** min(task.request.retries, 5)), max_retries=5)

    # No cache bump here: during a sale that would invalidate every cached
    # report on every order. The resolver bumps once per debounced run.
    schedule_recompute(order.event)


@app.task(bind=True, max_retries=3, acks_late=True, queue="background")
def process_order_paid(self, order_pk: int):
    """Full ingestion for a newly paid order."""
    _ingest(self, order_pk)


@app.task(bind=True, max_retries=3, acks_late=True, queue="background")
def process_order_canceled(self, order_pk: int):
    """
    Re-ingest a canceled/refunded order. Facts are kept (historical data is
    valuable) with status, refund amount and cancellation date updated.
    """
    _ingest(self, order_pk)


@app.task(bind=True, max_retries=3, acks_late=True, queue="background")
def process_order_changed(self, order_pk: int):
    """Re-ingest after an order change (products swapped, attendee edited…)."""
    _ingest(self, order_pk)


@app.task(bind=True, max_retries=RESYNC_WAIT_RETRIES, acks_late=True, queue="background")
def process_checkin_created(self, order_pk: int, attempt: int = 0):
    """
    Mark the checked-in tickets and recompute the predictive score.

    If the AnalyticsOrderFact does not yet exist — e.g. the check-in arrives
    before the order_paid task has finished — requeue with backoff so the
    check-in isn't lost.
    """
    from django.db.models import Q
    from django_scopes import scopes_disabled
    from pretix.base.models import Order

    from .models import AnalyticsOrderFact, AnalyticsTicketFact
    from .services.ingest import load_checkins
    from .services.predictor import score_from_fact
    from .services.people import schedule_recompute
    from .services.resync_service import is_resync_in_progress

    with scopes_disabled():
        order = Order.objects.select_related("event").filter(pk=order_pk).first()
    if order is None:
        return

    if is_resync_in_progress(order.event.pk):
        raise self.retry(countdown=60, max_retries=RESYNC_WAIT_RETRIES)

    fact = AnalyticsOrderFact.objects.filter(event=order.event, order_code=order.code).first()
    if fact is None:
        # Own attempt counter: resync deferrals above must not use up the
        # budget for waiting on the order_paid ingestion.
        if attempt < 5:
            logger.info("analytics: fact not yet created for order %s — requeueing checkin.", order.code)
            process_checkin_created.apply_async(args=[order_pk], kwargs={"attempt": attempt + 1},
                                                countdown=60 * (2 ** attempt))
            return
        logger.warning("analytics: gave up updating checkin for order %s.", order.code)
        return

    checkins = load_checkins([order.pk])
    for tf in AnalyticsTicketFact.objects.filter(order_fact=fact).filter(Q(position_id__in=list(checkins))):
        if not tf.checked_in or tf.first_checkin_at != checkins[tf.position_id]:
            tf.checked_in = True
            tf.first_checkin_at = checkins[tf.position_id]
            tf.save(update_fields=["checked_in", "first_checkin_at"])

    fact.checkin_completed = True
    fact.predicted_repeat_probability = score_from_fact(fact)
    fact.save(update_fields=["checkin_completed", "predicted_repeat_probability", "updated_at"])
    schedule_recompute(order.event)


@app.task(bind=True, max_retries=3, acks_late=True, queue="background")
def recompute_people_scope(self, organizer_id: int, series_slug: str, event_id=None):
    """Series-wide identity resolution (see services.people)."""
    from django.core.cache import cache

    from .services.people import pending_key, run_scope

    # Clear first: orders arriving while we run schedule a fresh pass.
    cache.delete(pending_key(organizer_id, series_slug, event_id))
    try:
        run_scope(organizer_id, series_slug, event_id)
    except Exception as exc:
        logger.exception("analytics: people recompute failed for %s/%s", organizer_id, series_slug or event_id)
        raise self.retry(exc=exc, countdown=120 * (2 ** self.request.retries))


@app.task(bind=True, max_retries=3, acks_late=True, queue="background")
def recompute_people(self, event_pk: int):
    """Pre-2.0 message format (still in queues after an upgrade)."""
    from django_scopes import scopes_disabled
    from pretix.base.models import Event

    from .services.people import run_scope, scope_of

    with scopes_disabled():
        event = Event.objects.filter(pk=event_pk).first()
    if event is not None:
        run_scope(*scope_of(event))


@app.task(bind=True, max_retries=3, acks_late=True, queue="background")
def trigger_event_resync(self, event_pk: int, include_checkin: bool = True):
    """UI-triggered full resync for a single event."""
    from django.core.cache import cache
    from django_scopes import scopes_disabled
    from pretix.base.models import Event

    from .services.resync_service import resync_event

    with scopes_disabled():
        event = Event.objects.select_related("organizer").filter(pk=event_pk).first()
    if event is None:
        logger.warning("analytics: event %s not found for resync.", event_pk)
        return

    logger.info("analytics: starting UI-triggered resync for %s", event.slug)
    try:
        result = resync_event(
            event,
            log_fn=lambda m: logger.info("analytics resync [%s]: %s", event.slug, m),
        )
        logger.info("analytics: resync complete for %s — %s", event.slug, result)
    except Exception as exc:
        logger.exception("analytics: UI resync failed for event %s", event.slug)
        raise self.retry(exc=exc, countdown=120 * (2 ** self.request.retries))
    finally:
        # Release the button lock set by TriggerResyncView.
        cache.delete(f"analytics_resync_lock_{event_pk}")


@app.task(bind=True, max_retries=1, acks_late=True, queue="background")
def trigger_series_resync(self, series_pk: int):
    """Resync every edition of a series, then resolve people once."""
    from .models import EventSeries
    from .services.resync_service import resync_series

    series = EventSeries.objects.filter(pk=series_pk).first()
    if series is None:
        return
    resync_series(series, log_fn=lambda m: logger.info("analytics series resync [%s]: %s", series.slug, m))
