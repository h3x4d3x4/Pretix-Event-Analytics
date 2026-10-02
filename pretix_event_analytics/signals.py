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
    checkin_created, event_copy_data, order_canceled, order_changed, order_modified, order_paid, order_reactivated,
    order_split, periodic_task,
)
from pretix.control.signals import event_dashboard_widgets, nav_event, nav_event_settings, nav_organizer

from ._compat import CHANGE_EVENT_SETTINGS, CHANGE_ORGANIZER_SETTINGS, VIEW_ORDERS

logger = logging.getLogger(__name__)


# ── Order lifecycle ───────────────────────────────────────────────────────────

@receiver(order_paid, dispatch_uid="pretix_event_analytics_order_paid")
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


@receiver(order_canceled, dispatch_uid="pretix_event_analytics_order_canceled")
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


@receiver(order_changed, dispatch_uid="pretix_event_analytics_order_changed")
def on_order_changed(sender, order, **kwargs):
    """Products/prices changed — re-ingest so ticket facts stay accurate."""
    _queue_change(order.pk)


@receiver(order_modified, dispatch_uid="pretix_event_analytics_order_modified")
def on_order_modified(sender, order, **kwargs):
    """Attendee data/answers edited — re-ingest identities and demographics."""
    _queue_change(order.pk)


@receiver(order_reactivated, dispatch_uid="pretix_event_analytics_order_reactivated")
def on_order_reactivated(sender, order, **kwargs):
    _queue_change(order.pk)


@receiver(order_split, dispatch_uid="pretix_event_analytics_order_split")
def on_order_split(sender, original, split_order, **kwargs):
    _queue_change(original.pk)
    _queue_change(split_order.pk)


@receiver(checkin_created, dispatch_uid="pretix_event_analytics_checkin_created")
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


@receiver(periodic_task, dispatch_uid="pretix_event_analytics_periodic")
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

@receiver(event_dashboard_widgets, dispatch_uid="pretix_event_analytics_dashboard_widgets")
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
    from django.utils.html import format_html

    def widget(num, text):
        # Pretix 2026.7 escapes widget content unless it is marked safe;
        # format_html escapes the values and marks the result safe.
        return format_html('<div class="numwidget"><span class="num">{}</span><span class="text">{}</span></div>', num, text)
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
        {"content": widget(f"{returning}%", _("Returning buyers")),
         "lazy": "analytics-returning", "display_size": "small", "priority": 50, "url": loyalty_url},
        {"content": widget(f"{first_time}%", _("First-time attendees")),
         "lazy": "analytics-firsttime", "display_size": "small", "priority": 49, "url": loyalty_url},
    ]


# ── Navigation ────────────────────────────────────────────────────────────────

@receiver(nav_event, dispatch_uid="pretix_event_analytics_nav_event")
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


@receiver(nav_event_settings, dispatch_uid="pretix_event_analytics_nav_event_settings")
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


@receiver(nav_organizer, dispatch_uid="pretix_event_analytics_nav_organizer")
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


# ── Event copy ────────────────────────────────────────────────────────────────

@receiver(event_copy_data, dispatch_uid="pretix_event_analytics_event_copy")
def copy_analytics_config(sender, other, question_map=None, **kwargs):
    """A copied event (usually next year's edition) joins the same series with the same
    settings. Edition year comes from the new event's date; sales targets, alert state and
    moments belong to one edition and are not copied."""
    from .models import EventAnalyticsConfig

    src = EventAnalyticsConfig.objects.filter(event=other).first()
    if src is None:
        return
    question_map = question_map or {}

    def mapped(qid):
        q = question_map.get(qid)
        return q.pk if q is not None else None

    EventAnalyticsConfig.objects.update_or_create(event=sender, defaults={
        "series": src.series,
        "edition_year": sender.date_from.year if sender.date_from else src.edition_year + 1,
        "home_country": src.home_country,
        "is_active": src.is_active,
        "tracked_question_ids": [m for m in (mapped(q) for q in src.tracked_question_ids or []) if m],
        "id_question_id": mapped(src.id_question_id) if src.id_question_id else None,
        "pace_alert_threshold": src.pace_alert_threshold,
        "pace_alert_recipients": src.pace_alert_recipients,
    })


# ── Activity log ──────────────────────────────────────────────────────────────
# Admin actions appear in pretix's event / organizer log. Entries carry settings and
# counts only — never personal data. (The log is pretix's own table; ticketing data is
# still never written.)

from django.utils.translation import gettext_lazy  # noqa: E402
from pretix.base.logentrytype_registry import LogEntryType, log_entry_types  # noqa: E402
from pretix.base.logentrytypes import EventLogEntryType  # noqa: E402


@log_entry_types.new_from_dict({
    "pretix_event_analytics.config.changed": gettext_lazy("Analytics settings were changed."),
    "pretix_event_analytics.resync": gettext_lazy("An analytics resync was started."),
    "pretix_event_analytics.moment.added": gettext_lazy("A sales moment was added to the analytics timeline: {label}"),
    "pretix_event_analytics.moment.deleted": gettext_lazy("A sales moment was removed from the analytics timeline: {label}"),
})
class AnalyticsEventLogEntryType(EventLogEntryType):
    pass


@log_entry_types.new_from_dict({
    "pretix_event_analytics.series.added": gettext_lazy("Analytics series “{name}” was created."),
    "pretix_event_analytics.series.changed": gettext_lazy("Analytics series “{name}” was changed."),
    "pretix_event_analytics.series.deleted": gettext_lazy("Analytics series “{name}” was deleted."),
    "pretix_event_analytics.series.resync": gettext_lazy("A resync of analytics series “{name}” was started."),
    "pretix_event_analytics.legacy.imported": gettext_lazy(
        "Past attendee list “{label}” ({year}) was imported into analytics series “{series}”: {count} entries, "
        "stored as hashes only."),
    "pretix_event_analytics.legacy.deleted": gettext_lazy(
        "Past attendee list “{label}” was removed from analytics series “{series}”."),
})
class AnalyticsOrganizerLogEntryType(LogEntryType):
    pass
