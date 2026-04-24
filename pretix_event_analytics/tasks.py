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
models (AnalyticsOrderFact, AnalyticsTicketFact, AnalyticsIdentity,
EventSeries, EventAnalyticsConfig). This invariant is enforced statically
by ``scripts/check_isolation.py``.
"""
import logging

from pretix.celery_app import app

logger = logging.getLogger(__name__)


@app.task(bind=True, max_retries=3, acks_late=True, queue="background")
def process_order_paid(self, order_pk: int):
    """
    Full analytics ingestion pipeline for a newly paid order.

    Steps:
      1. If a resync is in flight for this event, requeue and return
      2. Load order with all related data in a single query
      3. Check EventAnalyticsConfig exists (silently skip if not configured)
      4. Normalize order into fact dicts (ValueError → skip; other → retry)
      5. Run repeat detection (inside an atomic block)
      6. Calculate predictive score
      7. Upsert AnalyticsOrderFact + AnalyticsTicketFact
    """
    from django.db import transaction

    from django_scopes import scopes_disabled
    from pretix.base.models import Order

    from .models import AnalyticsIdentity, AnalyticsOrderFact, AnalyticsTicketFact, EventAnalyticsConfig
    from .services.normalizer import normalize_order
    from .services.predictor import calculate_repeat_probability
    from .services.repeat_detector import evaluate_repeat_status
    from .services.resync_service import is_resync_in_progress

    with scopes_disabled():
        try:
            order = (
                Order.objects.select_related(
                    "event__organizer",
                    "invoice_address",
                )
                .prefetch_related(
                    "positions__item",
                    "positions__variation",
                    "positions__answers__question",
                    "payments",
                    "refunds",
                )
                .get(pk=order_pk)
            )
        except Order.DoesNotExist:
            logger.info(
                "analytics: Order %s not found (may have been deleted), skipping.",
                order_pk,
            )
            return

    # Defer ingestion while a full resync is rebuilding this event's facts.
    # Otherwise we'd read an empty table mid-rebuild and set is_repeat_buyer
    # to False incorrectly.
    if is_resync_in_progress(order.event.pk):
        logger.info(
            "analytics: resync in progress for event %s — requeueing order %s in 60s.",
            order.event.slug, order_pk,
        )
        raise self.retry(countdown=60, max_retries=20)

    try:
        config = EventAnalyticsConfig.objects.select_related("series").get(
            event=order.event
        )
    except EventAnalyticsConfig.DoesNotExist:
        # Plugin not configured for this event — skip silently
        return

    try:
        order_fact_data, ticket_facts_data = normalize_order(order, config)
    except ValueError as exc:
        # Expected: malformed / unsupported order — skip, do not retry.
        logger.warning(
            "analytics: skipping order %s (normalization rejected): %s",
            order_pk, exc,
        )
        return
    except Exception as exc:
        # Unexpected: transient DB / code bug — retry with backoff.
        logger.exception("analytics: normalization failed for order %s", order_pk)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))

    # Extract internal keys AFTER normalization succeeds (safe to consume)
    identities = order_fact_data.pop("_identities", [])
    order_fact_data.pop("_repeat_hashes", None)
    is_local_buyer = order_fact_data.pop("_is_local_buyer", False)
    bought_early = order_fact_data.pop("_bought_early", False)

    # Entire read-compute-write cycle in one transaction to serialise
    # concurrent repeat detection for the same buyer.
    with transaction.atomic():
        # Run repeat detection using identity-based matching
        if identities:
            repeat_status = evaluate_repeat_status(order.event, identities)
            order_fact_data.update(repeat_status)

        # Calculate predictive probability
        probability = calculate_repeat_probability(
            {
                "repeat_count": order_fact_data.get("repeat_count", 0),
                "ticket_count": order_fact_data.get("ticket_count", 1),
                "is_group_order": order_fact_data.get("is_group_order", False),
                "is_local_buyer": is_local_buyer,
                "bought_early": bought_early,
                "checkin_completed": False,  # Not possible at payment time
            }
        )
        order_fact_data["predicted_repeat_probability"] = probability

        # Upsert AnalyticsOrderFact
        fact, created = AnalyticsOrderFact.objects.update_or_create(
            event=order.event,
            order_code=order.code,
            defaults=order_fact_data,
        )

        # Replace ticket facts entirely (clean + re-insert)
        AnalyticsTicketFact.objects.filter(order_fact=fact).delete()

        ticket_objs = [
            AnalyticsTicketFact(order_fact=fact, event=order.event, **tf_data)
            for tf_data in ticket_facts_data
        ]
        AnalyticsTicketFact.objects.bulk_create(ticket_objs, ignore_conflicts=True)

        # Replace identity records for repeat detection
        AnalyticsIdentity.objects.filter(order_fact=fact).delete()
        if identities:
            AnalyticsIdentity.objects.bulk_create(
                [
                    AnalyticsIdentity(
                        order_fact=fact,
                        event=order.event,
                        identity_type=i["type"],
                        identity_hash=i["hash"],
                    )
                    for i in identities
                ],
                ignore_conflicts=True,
            )

    # Invalidate cohort cache when a new order affects the series
    if config.series:
        from .services.cohort_service import invalidate_cohort_cache
        invalidate_cohort_cache(config.series.slug, order.event.organizer_id)

    logger.debug(
        "analytics: ingested order %s (%s), repeat=%s, score=%s, identities=%d",
        order.code,
        "created" if created else "updated",
        order_fact_data.get("is_repeat_buyer"),
        probability,
        len(identities),
    )


@app.task(bind=True, max_retries=3, acks_late=True, queue="background")
def process_order_canceled(self, order_pk: int):
    """
    Update the analytics fact for a canceled or refunded order.

    Does NOT delete the fact — historical data is valuable.
    Sets is_refunded=True if money was returned, updates order_status.
    """
    from django_scopes import scopes_disabled
    from pretix.base.models import Order

    from .models import AnalyticsOrderFact
    from .services.resync_service import is_resync_in_progress

    with scopes_disabled():
        try:
            order = Order.objects.prefetch_related("refunds").get(pk=order_pk)
        except Order.DoesNotExist:
            return

    if is_resync_in_progress(order.event.pk):
        raise self.retry(countdown=60, max_retries=20)

    is_refunded = order.refunds.filter(state__in=("done", "transit")).exists()

    updated = AnalyticsOrderFact.objects.filter(
        event=order.event, order_code=order.code
    ).update(
        order_status=order.status,
        is_refunded=is_refunded,
    )

    if updated:
        logger.debug(
            "analytics: updated canceled/refunded order %s (is_refunded=%s)",
            order.code,
            is_refunded,
        )


@app.task(bind=True, max_retries=3, acks_late=True, queue="background")
def process_checkin_created(self, order_pk: int):
    """
    Mark checkin_completed=True and recompute predictive score.
    Triggered when an attendee checks in at the event.

    If the AnalyticsOrderFact does not yet exist — e.g. the check-in
    arrives before the order_paid task has finished — we requeue with a
    small backoff so the check-in isn't lost.
    """
    from django_scopes import scopes_disabled
    from pretix.base.models import Order

    from .models import AnalyticsOrderFact, EventAnalyticsConfig
    from .services.predictor import calculate_repeat_probability
    from .services.resync_service import is_resync_in_progress

    with scopes_disabled():
        try:
            order = Order.objects.select_related("event").get(pk=order_pk)
        except Order.DoesNotExist:
            logger.info(
                "analytics: checkin task for order %s — order not found, skipping.",
                order_pk,
            )
            return

    if is_resync_in_progress(order.event.pk):
        raise self.retry(countdown=60, max_retries=20)

    try:
        fact = AnalyticsOrderFact.objects.get(event=order.event, order_code=order.code)
    except AnalyticsOrderFact.DoesNotExist:
        # Fact hasn't been created yet — most likely a race with the
        # paid-order ingestion task. Requeue with exponential backoff so
        # the checkin eventually lands once the fact is in place.
        if self.request.retries < self.max_retries:
            logger.info(
                "analytics: fact not yet created for order %s — requeueing checkin update.",
                order.code,
            )
            raise self.retry(countdown=60 * (2 ** self.request.retries))
        logger.warning(
            "analytics: gave up updating checkin for order %s — no AnalyticsOrderFact after %d retries.",
            order.code, self.max_retries,
        )
        return

    try:
        config = EventAnalyticsConfig.objects.select_related("series").get(
            event=order.event
        )
    except EventAnalyticsConfig.DoesNotExist:
        return

    home_country = (config.home_country or "").upper()
    is_local = bool(home_country) and fact.country_code == home_country

    # Recalculate with checkin=True
    probability = calculate_repeat_probability(
        {
            "repeat_count": fact.repeat_count,
            "ticket_count": fact.ticket_count,
            "is_group_order": fact.is_group_order,
            "is_local_buyer": is_local,
            "bought_early": False,  # Unknown at this point, keep conservative
            "checkin_completed": True,
        }
    )

    AnalyticsOrderFact.objects.filter(pk=fact.pk).update(
        checkin_completed=True,
        predicted_repeat_probability=probability,
    )


@app.task(bind=True, max_retries=3, acks_late=True, queue="background")
def trigger_event_resync(self, event_pk: int, include_checkin: bool = False):
    """
    UI-triggered full resync for a single event.
    Called by TriggerResyncView when the organiser presses the Resync button.

    Uses the shared resync_service so there is a single code path.
    """
    from django_scopes import scopes_disabled
    from pretix.base.models import Event

    from .services.resync_service import resync_event

    with scopes_disabled():
        try:
            event = Event.objects.select_related("organizer").get(pk=event_pk)
        except Event.DoesNotExist:
            logger.warning("analytics: event %s not found for resync.", event_pk)
            return

    logger.info("analytics: starting UI-triggered resync for %s", event.slug)
    try:
        result = resync_event(
            event,
            include_checkin=include_checkin,
            log_fn=lambda m: logger.info("analytics resync [%s]: %s", event.slug, m),
        )
        logger.info(
            "analytics: resync complete for %s — %s processed, %s skipped",
            event.slug,
            result["processed"],
            result["skipped"],
        )
    except Exception as exc:
        logger.exception("analytics: UI resync failed for event %s", event.slug)
        raise self.retry(exc=exc, countdown=120 * (2 ** self.request.retries))
