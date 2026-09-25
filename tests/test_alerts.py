"""Pace alerts."""
import datetime

import pytest
from django.core import mail as djmail
from django.utils import timezone

from pretix_event_analytics.services.alerts import pace_status, run_pace_alerts
from pretix_event_analytics.services.resync_service import resync_series


@pytest.fixture
def editions(make_edition, series):
    now = timezone.now()
    prev = make_edition(2025, date=now - datetime.timedelta(days=300))
    cur = make_edition(2026, date=now + datetime.timedelta(days=30))
    # Previous edition: 30 tickets sold 40+ days out
    for i in range(30):
        prev.order(f"p{i}@example.org", days_before=45)
    return prev, cur


def _configure(cur, threshold=20, recipients="team@example.org"):
    cfg = cur.event.analytics_config
    cfg.pace_alert_threshold = threshold
    cfg.pace_alert_recipients = recipients
    cfg.save()
    return cfg


def test_alert_sent_when_behind_and_capped_weekly(editions, series):
    prev, cur = editions
    for i in range(12):  # 12 vs 30 → 60% behind
        cur.order(f"c{i}@example.org", days_before=40)
    resync_series(series)
    cfg = _configure(cur)
    status = pace_status(cfg)
    assert (status["current"], status["previous"], status["gap_pct"]) == (12, 30, 60.0)
    djmail.outbox = []
    assert run_pace_alerts() == 1
    assert len(djmail.outbox) == 1
    msg = djmail.outbox[0]
    assert msg.to == ["team@example.org"] and "60.0%" in msg.subject
    assert run_pace_alerts() == 0  # not again within a week


def test_no_alert_when_on_pace(editions, series):
    prev, cur = editions
    for i in range(29):  # 29 vs 30 → 3% behind, threshold 20%
        cur.order(f"c{i}@example.org", days_before=40)
    resync_series(series)
    _configure(cur)
    djmail.outbox = []
    assert run_pace_alerts() == 0 and djmail.outbox == []


def test_no_alert_without_enough_history(make_edition, series):
    now = timezone.now()
    prev = make_edition(2025, date=now - datetime.timedelta(days=300))
    cur = make_edition(2026, date=now + datetime.timedelta(days=30))
    prev.order("only@example.org", days_before=45)
    resync_series(series)
    _configure(cur)
    assert run_pace_alerts() == 0


def test_config_form_validates_recipients(admin_client, make_edition):
    from django.urls import reverse
    kit = make_edition(2026)
    url = reverse("plugins:pretix_event_analytics:config",
                  kwargs={"organizer": kit.event.organizer.slug, "event": kit.event.slug})
    base = {"series": kit.event.analytics_config.series_id, "edition_year": 2026, "is_active": "on"}
    r = admin_client.post(url, {**base, "pace_alert_threshold": 15})
    assert r.status_code == 200 and "Add at least one address" in r.content.decode()
    r = admin_client.post(url, {**base, "pace_alert_threshold": 15, "pace_alert_recipients": "a@example.org, b@example.org"})
    assert r.status_code == 302
    kit.event.analytics_config.refresh_from_db()
    assert kit.event.analytics_config.pace_alert_recipients == "a@example.org\nb@example.org"
