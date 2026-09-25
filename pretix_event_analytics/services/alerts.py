"""
Pace alerts: an e-mail when an upcoming edition sells noticeably slower than
the previous edition did at the same number of days before its event.

Runs from Pretix's periodic task. At most one alert per edition every
``RESEND_DAYS`` while sales stay behind; nothing is sent once the edition
catches up (and the next dip alerts again).
"""
import datetime
import logging
from typing import Dict, Optional

from django.db.models import Sum
from django.utils.timezone import now
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

RESEND_DAYS = 7
MIN_PREVIOUS_TICKETS = 20  # too few sales to compare meaningfully


def _tickets_by(event_id: int, days_left: int) -> int:
    from ..models import AnalyticsOrderFact

    return AnalyticsOrderFact.objects.filter(
        event_id=event_id, order_status="p", is_refunded=False, days_before_event__gte=days_left,
    ).aggregate(n=Sum("ticket_count"))["n"] or 0


def previous_edition(config):
    from ..models import EventAnalyticsConfig

    if not config.series:
        return None
    earlier = [
        c for c in EventAnalyticsConfig.objects.filter(series=config.series, is_active=True)
        .select_related("event").order_by("edition_year", "event__date_from", "event_id")
        if (c.edition_year, c.event.date_from) < (config.edition_year, config.event.date_from)
        and c.event_id != config.event_id
    ]
    return earlier[-1] if earlier else None


def pace_status(config) -> Optional[Dict]:
    """Current vs previous edition at the same number of days before the event."""
    event = config.event
    if not event.date_from or event.date_from <= now():
        return None
    prev = previous_edition(config)
    if prev is None:
        return None
    tz = event.timezone
    days_left = (event.date_from.astimezone(tz).date() - now().astimezone(tz).date()).days
    current = _tickets_by(event.pk, days_left)
    previous = _tickets_by(prev.event_id, days_left)
    if previous < MIN_PREVIOUS_TICKETS:
        return None
    return {
        "days_left": days_left,
        "current": current,
        "previous": previous,
        "previous_label": str(prev.event.name),
        "gap_pct": round((previous - current) / previous * 100, 1),
    }


def _recipients(raw: str):
    import re

    return [a for a in re.split(r"[\s,;]+", raw or "") if "@" in a]


def run_pace_alerts() -> int:
    """Check every configured edition; returns the number of alerts sent."""
    from django_scopes import scopes_disabled
    from pretix.base.services.mail import mail

    from ..models import EventAnalyticsConfig

    sent = 0
    with scopes_disabled():
        configs = (EventAnalyticsConfig.objects.filter(pace_alert_threshold__isnull=False)
                   .exclude(pace_alert_recipients="").select_related("event__organizer", "series"))
        for cfg in configs:
            if cfg.pace_alert_last_sent and now() - cfg.pace_alert_last_sent < datetime.timedelta(days=RESEND_DAYS):
                continue
            status = pace_status(cfg)
            if not status or status["gap_pct"] < cfg.pace_alert_threshold:
                continue
            recipients = _recipients(cfg.pace_alert_recipients)
            if not recipients:
                continue
            from django.conf import settings
            from django.urls import reverse

            url = settings.SITE_URL + reverse("plugins:pretix_event_analytics:sales", kwargs={
                "organizer": cfg.event.organizer.slug, "event": cfg.event.slug})
            try:
                mail(
                    recipients,
                    _("{event}: ticket sales {gap}% behind {previous}").format(
                        event=cfg.event.name, gap=status["gap_pct"], previous=status["previous_label"]),
                    "pretix_event_analytics/email/pace_alert.txt",
                    {"event": cfg.event, "status": status, "threshold": cfg.pace_alert_threshold, "url": url},
                    event=cfg.event,
                    locale=cfg.event.settings.locale,
                )
            except Exception:
                logger.exception("analytics: pace alert for %s could not be sent", cfg.event.slug)
                continue
            EventAnalyticsConfig.objects.filter(pk=cfg.pk).update(pace_alert_last_sent=now())
            sent += 1
    return sent
