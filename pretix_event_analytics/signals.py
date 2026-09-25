"""
Signal handlers — bridges Pretix events to our async analytics tasks.

All handlers are intentionally thin: they validate minimally and dispatch
a Celery task.  No DB work happens in the signal handler itself.

Signals used:
  order_paid          — new paid order → full ingestion pipeline
  order_canceled      — canceled or refunded → update fact record
  order_changed / order_modified / order_reactivated / order_split
                      — re-ingest the affected order(s)
  checkin_created     — attendee checked in → update score

Navigation:
  nav_event           — injects the Analytics link into the event sidebar
  nav_event_settings  — injects the Analytics Settings link in event Settings tab
  nav_organizer       — injects Series Management into organiser sidebar
"""
import logging

from django.dispatch import receiver
from django.urls import reverse

from pretix.base.signals import (
    periodic_task,
    checkin_created,
    order_canceled,
    order_changed,
    order_modified,
    order_paid,
    order_reactivated,
    order_split,
)
from pretix.control.signals import event_dashboard_widgets, nav_event, nav_event_settings, nav_organizer
from ._compat import CHANGE_EVENT_SETTINGS, CHANGE_ORGANIZER_SETTINGS, VIEW_ORDERS

logger = logging.getLogger(__name__)


# ── Order lifecycle ───────────────────────────────────────────────────────────

@receiver(order_paid, dispatch_uid="pretix_analytics_order_paid")
def on_order_paid(sender, order, **kwargs):
    """Queue analytics ingestion when an order is paid."""
    from .tasks import process_order_paid

    try:
        process_order_paid.apply_async(
            args=[order.pk],
            countdown=5,  # Brief delay so all related objects are committed
        )
    except Exception:
        logger.exception("analytics: failed to queue order_paid task for order %s", order.pk)


@receiver(order_canceled, dispatch_uid="pretix_analytics_order_canceled")
def on_order_canceled(sender, order, **kwargs):
    """Update analytics fact when an order is canceled or refunded."""
    from .tasks import process_order_canceled

    try:
        process_order_canceled.apply_async(args=[order.pk])
    except Exception:
        logger.exception("analytics: failed to queue order_canceled task for order %s", order.pk)


def _queue_change(order_pk):
    from .tasks import process_order_changed

    try:
        process_order_changed.apply_async(args=[order_pk], countdown=5)
    except Exception:
        logger.exception("analytics: failed to queue order change task for order %s", order_pk)


@receiver(order_changed, dispatch_uid="pretix_analytics_order_changed")
def on_order_changed(sender, order, **kwargs):
    """Products/prices changed — re-ingest so ticket facts stay accurate."""
    _queue_change(order.pk)


@receiver(order_modified, dispatch_uid="pretix_analytics_order_modified")
def on_order_modified(sender, order, **kwargs):
    """Attendee data/answers edited — re-ingest identities and demographics."""
    _queue_change(order.pk)


@receiver(order_reactivated, dispatch_uid="pretix_analytics_order_reactivated")
def on_order_reactivated(sender, order, **kwargs):
    _queue_change(order.pk)


@receiver(order_split, dispatch_uid="pretix_analytics_order_split")
def on_order_split(sender, original, split_order, **kwargs):
    _queue_change(original.pk)
    _queue_change(split_order.pk)


@receiver(checkin_created, dispatch_uid="pretix_analytics_checkin_created")
def on_checkin_created(sender, checkin, **kwargs):
    """
    Recompute predictive score when an attendee checks in.
    checkin.position.order_id gives us the order PK.
    """
    from .tasks import process_checkin_created

    try:
        order_pk = checkin.position.order_id
        process_checkin_created.apply_async(args=[order_pk])
    except Exception:
        logger.exception("analytics: failed to queue checkin update")


@receiver(periodic_task, dispatch_uid="pretix_analytics_periodic")
def on_periodic_task(sender, **kwargs):
    """
    Safety net run by Pretix's cron: resolve returning people for any series
    whose facts changed since the last resolution. This is how installs
    without a Celery worker stay correct without doing heavy work inside a
    buyer's payment request.
    """
    from .services.alerts import run_pace_alerts
    from .services.people import resolve_dirty_scopes

    try:
        resolve_dirty_scopes()
    except Exception:
        logger.exception("analytics: periodic people resolution failed")
    try:
        run_pace_alerts()
    except Exception:
        logger.exception("analytics: pace alerts failed")


# ── Pretix event dashboard ─────────────────────────────────────────────────

@receiver(event_dashboard_widgets, dispatch_uid="pretix_analytics_dashboard_widgets")
def analytics_dashboard_widgets(sender, subevent=None, lazy=False, **kwargs):
    """
    Headline figures on Pretix's own event dashboard. Pretix only asks for
    widgets when the user can view this event's orders, and no request is
    passed — so widgets use this event's facts only, never other editions.
    """
    from django.db.models import Count, Q
    from django.utils.translation import gettext as _

    from .models import AnalyticsOrderFact, AnalyticsTicketFact, EventAnalyticsConfig

    if subevent or not EventAnalyticsConfig.objects.filter(event=sender).exists():
        return []
    loyalty_url = reverse("plugins:pretix_event_analytics:loyalty",
                          kwargs={"organizer": sender.organizer.slug, "event": sender.slug})
    widget = '<div class="numwidget"><span class="num">{num}</span><span class="text">{text}</span></div>'
    if lazy:
        return [
            {"content": None, "lazy": "analytics-returning", "display_size": "small", "priority": 50, "url": loyalty_url},
            {"content": None, "lazy": "analytics-firsttime", "display_size": "small", "priority": 49, "url": loyalty_url},
        ]
    orders = AnalyticsOrderFact.objects.filter(event=sender, order_status="p", is_refunded=False).aggregate(
        identified=Count("id", filter=~Q(person_key="")),
        returning=Count("id", filter=Q(is_repeat_buyer=True)),
    )
    tickets = AnalyticsTicketFact.objects.filter(
        event=sender, is_addon=False, order_fact__order_status="p", order_fact__is_refunded=False,
    ).exclude(attendee_person_key="").aggregate(n=Count("id"), new=Count("id", filter=Q(is_returning_attendee=False)))
    returning = round(orders["returning"] / orders["identified"] * 100) if orders["identified"] else 0
    first_time = round(tickets["new"] / tickets["n"] * 100) if tickets["n"] else 0
    return [
        {"content": widget.format(num=f"{returning}%", text=_("Returning buyers")),
         "lazy": "analytics-returning", "display_size": "small", "priority": 50, "url": loyalty_url},
        {"content": widget.format(num=f"{first_time}%", text=_("First-time attendees")),
         "lazy": "analytics-firsttime", "display_size": "small", "priority": 49, "url": loyalty_url},
    ]


# ── Navigation ────────────────────────────────────────────────────────────────

@receiver(nav_event, dispatch_uid="pretix_analytics_nav_event")
def add_analytics_nav(sender, request=None, **kwargs):
    """
    Add Analytics (with one child per dashboard page) to the event sidebar.
    Only shown to users with can_view_orders permission.
    """
    from django.utils.translation import gettext_lazy as _

    if not request or not getattr(request, "event", None):
        return []
    if not request.user.has_event_permission(request.organizer, request.event, VIEW_ORDERS, request):
        return []

    url_name = getattr(getattr(request, "resolver_match", None), "url_name", "") or ""
    in_plugin = "pretix_event_analytics" in (getattr(request.resolver_match, "namespace", "") or "")

    def url(name):
        return reverse(f"plugins:pretix_event_analytics:{name}",
                       kwargs={"organizer": request.organizer.slug, "event": request.event.slug})

    pages = [
        ("dashboard", _("Overview")), ("sales", _("Sales")), ("audience", _("Audience")),
        ("loyalty", _("Loyalty")), ("tickets", _("Tickets")), ("operations", _("Operations")),
        ("resale", _("Resale")),
    ]
    return [{
        "label": _("Analytics"),
        "url": url("dashboard"),
        "icon": "bar-chart",
        "active": in_plugin and url_name != "config",
        "children": [
            {"label": label, "url": url(name), "active": in_plugin and url_name == name}
            for name, label in pages
        ],
    }]


@receiver(nav_event_settings, dispatch_uid="pretix_analytics_nav_event_settings")
def add_analytics_settings_nav(sender, request=None, **kwargs):
    """
    Add the Analytics Configuration link to the event Settings sidebar tab.
    Only shown to users with can_change_event_settings permission.
    """
    if not request or not request.event:
        return []
    if not request.user.has_event_permission(
        request.organizer, request.event, CHANGE_EVENT_SETTINGS, request
    ):
        return []

    url = reverse(
        "plugins:pretix_event_analytics:config",
        kwargs={
            "organizer": request.organizer.slug,
            "event": request.event.slug,
        },
    )
    from django.utils.translation import gettext_lazy as _

    return [
        {
            "label": _("Analytics"),
            "url": url,
            "active": request.path.startswith(url),
        }
    ]


@receiver(nav_organizer, dispatch_uid="pretix_analytics_nav_organizer")
def add_organizer_nav(sender, request=None, **kwargs):
    """
    Add Analytics → Series Management to the organiser sidebar.
    Only shown to users with can_change_organizer_settings permission.
    """
    if not request or not request.organizer:
        return []
    if not request.user.has_organizer_permission(
        request.organizer, CHANGE_ORGANIZER_SETTINGS, request
    ):
        return []

    from django.utils.translation import gettext_lazy as _

    url = reverse(
        "plugins:pretix_event_analytics:series_list",
        kwargs={"organizer": request.organizer.slug},
    )
    return [
        {
            "label": _("Analytics series"),
            "url": url,
            "icon": "bar-chart",
            "active": "analytics/series" in request.path,
        }
    ]
