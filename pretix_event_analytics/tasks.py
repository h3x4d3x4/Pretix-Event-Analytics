"""
Celery tasks for asynchronous analytics processing.

Signal handlers must NOT do DB work inline — they queue these tasks instead.
This prevents request-cycle timeouts on large events (50k+ orders).

If Celery is not configured (single-server dev setups), Pretix falls back
to synchronous execution automatically.
"""
import logging

from pretix.celery_app import app

logger = logging.getLogger(__name__)


@app.task(bind=True, max_retries=3, default_retry_delay=60, queue="background")
def process_order_paid(self, order_pk: int):
    """
    Full analytics ingestion pipeline for a newly paid order.

    Steps:
      1. Load order with all related data in a single query
      2. Check EventAnalyticsConfig exists (silently skip if not configured)
      3. Normalize order into fact dicts
      4. Run repeat detection
      5. Calculate predictive score
      6. Upsert AnalyticsOrderFact + AnalyticsTicketFact
    """
    from django.db import transaction

    from django_scopes import scopes_disabled
    from pretix.base.models import Order

    from .models import AnalyticsIdentity, AnalyticsOrderFact, AnalyticsTicketFact, EventAnalyticsConfig
    from .services.normalizer import normalize_order
    from .services.predictor import calculate_repeat_probability
    from .services.repeat_detector import evaluate_repeat_status

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
            logger.warning("analytics: Order %s not found, skipping.", order_pk)
            return

    try:
        config = EventAnalyticsConfig.objects.select_related("series").get(
            event=order.event
        )
    except EventAnalyticsConfig.DoesNotExist:
        # Plugin not configured for this event — skip silently
        return

    try:
        order_fact_data, ticket_facts_data = normalize_order(order, config)
    except Exception as exc:
        logger.exception("analytics: normalization failed for order %s", order_pk)
        raise self.retry(exc=exc)

    # Extract internal keys before DB operations
    identities = order_fact_data.pop("_identities", [])
    order_fact_data.pop("_repeat_hashes", None)

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
            "is_local_buyer": order_fact_data.pop("_is_local_buyer", False),
            "bought_early": order_fact_data.pop("_bought_early", False),
            "checkin_completed": False,  # Not possible at payment time
        }
    )
    order_fact_data["predicted_repeat_probability"] = probability

    # All DB writes in a single atomic block to prevent partial state
    with transaction.atomic():
        # Upsert AnalyticsOrderFact
        fact, created = AnalyticsOrderFact.objects.update_or_create(
            event=order.event,
            order_code=order.code,
            defaults=order_fact_data,
        )

        # Replace ticket facts entirely (clean + re-insert)
        if not created:
            AnalyticsTicketFact.objects.filter(order_fact=fact).delete()

        ticket_objs = [
            AnalyticsTicketFact(order_fact=fact, event=order.event, **tf_data)
            for tf_data in ticket_facts_data
        ]
        AnalyticsTicketFact.objects.bulk_create(ticket_objs)

        # Create/replace identity records for repeat detection
        if not created:
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
                ]
            )

    logger.debug(
        "analytics: ingested order %s (%s), repeat=%s, score=%s, identities=%d",
        order.code,
        "created" if created else "updated",
        order_fact_data.get("is_repeat_buyer"),
        probability,
        len(identities),
    )


@app.task(bind=True, max_retries=3, default_retry_delay=60, queue="background")
def process_order_canceled(self, order_pk: int):
    """
    Update the analytics fact for a canceled or refunded order.

    Does NOT delete the fact — historical data is valuable.
    Sets is_refunded=True if money was returned, updates order_status.
    """
    from django_scopes import scopes_disabled
    from pretix.base.models import Order

    from .models import AnalyticsOrderFact

    with scopes_disabled():
        try:
            order = Order.objects.prefetch_related("refunds").get(pk=order_pk)
        except Order.DoesNotExist:
            return

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


@app.task(bind=True, max_retries=3, default_retry_delay=60, queue="background")
def process_checkin_created(self, order_pk: int):
    """
    Mark checkin_completed=True and recompute predictive score.
    Triggered when an attendee checks in at the event.
    """
    from django_scopes import scopes_disabled
    from pretix.base.models import Order

    from .models import AnalyticsOrderFact, EventAnalyticsConfig
    from .services.predictor import calculate_repeat_probability

    with scopes_disabled():
        try:
            order = Order.objects.select_related("event").get(pk=order_pk)
        except Order.DoesNotExist:
            return

    try:
        fact = AnalyticsOrderFact.objects.get(event=order.event, order_code=order.code)
    except AnalyticsOrderFact.DoesNotExist:
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


@app.task(bind=True, max_retries=3, default_retry_delay=120, queue="background")
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
        raise self.retry(exc=exc)
