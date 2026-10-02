"""Admin actions land in pretix's event / organizer activity log — without personal data."""
import datetime
import json

from django.urls import reverse
from django_scopes import scopes_disabled

from pretix_event_analytics.services.resync_service import resync_series


def _url(name, kit, **extra):
    return reverse(f"plugins:pretix_event_analytics:{name}",
                   kwargs={"organizer": kit.event.organizer.slug, "event": kit.event.slug, **extra})


def _org_url(name, organizer, **extra):
    return reverse(f"plugins:pretix_event_analytics:{name}", kwargs={"organizer": organizer.slug, **extra})


def _entries(obj, prefix="pretix_event_analytics."):
    with scopes_disabled():
        return list(obj.all_logentries().filter(action_type__startswith=prefix).order_by("pk"))


def test_config_change_is_logged_without_recipient_addresses(admin_client, make_edition, series):
    kit = make_edition(2026)
    resync_series(series)
    r = admin_client.post(_url("config", kit), {
        "series": series.pk, "edition_year": 2026, "home_country": "PT", "is_active": "on",
        "ticket_target": 900, "revenue_target": "", "pace_alert_threshold": 20,
        "pace_alert_recipients": "boss@example.org, ops@example.org",
    })
    assert r.status_code == 302
    (entry,) = _entries(kit.event)
    assert entry.action_type == "pretix_event_analytics.config.changed"
    data = json.loads(entry.data)
    assert data["ticket_target"] == 900 and data["pace_alert_recipients"] == 2
    assert "example.org" not in entry.data
    assert entry.display() == "Analytics settings were changed."

    # Saving again without changes adds nothing.
    admin_client.post(_url("config", kit), {
        "series": series.pk, "edition_year": 2026, "home_country": "PT", "is_active": "on",
        "ticket_target": 900, "revenue_target": "", "pace_alert_threshold": 20,
        "pace_alert_recipients": "boss@example.org, ops@example.org",
    })
    assert len(_entries(kit.event)) == 1


def test_moments_and_resync_are_logged(admin_client, make_edition, series):
    kit = make_edition(2026)
    kit.order("a@example.org", days_before=30)
    resync_series(series)
    day = (kit.event.date_from - datetime.timedelta(days=30)).date()
    admin_client.post(_url("annotations", kit), {"date": day.isoformat(), "label": "Line-up announced"})
    from pretix_event_analytics.models import SalesAnnotation
    note = SalesAnnotation.objects.get(event=kit.event)
    admin_client.post(_url("annotations", kit), {"delete": note.pk})
    admin_client.post(_url("trigger_resync", kit))
    actions = [e.action_type for e in _entries(kit.event)]
    assert actions == ["pretix_event_analytics.moment.added", "pretix_event_analytics.moment.deleted",
                       "pretix_event_analytics.resync"]
    assert _entries(kit.event)[0].display().endswith(": Line-up announced")


def test_series_and_legacy_actions_are_logged_on_the_organizer(admin_client, organizer, make_edition, series):
    make_edition(2026)
    admin_client.post(_org_url("series_create", organizer), {"name": "Other Fest", "slug": "other-fest"})
    admin_client.post(_org_url("legacy_import", organizer, pk=series.pk), {
        "label": "Festival 2019", "edition_year": 2019,
        "emails_text": "name;email\nAna Silva;ana@example.org\nRui Costa;rui@example.org",
    })
    from pretix_event_analytics.models import LegacyEdition
    le = LegacyEdition.objects.get(series=series)
    admin_client.post(_org_url("legacy_delete", organizer, pk=series.pk, legacy_pk=le.pk))
    admin_client.post(_org_url("series_resync", organizer, pk=series.pk))

    entries = _entries(organizer)
    assert [e.action_type for e in entries] == [
        "pretix_event_analytics.series.added", "pretix_event_analytics.legacy.imported",
        "pretix_event_analytics.legacy.deleted", "pretix_event_analytics.series.resync",
    ]
    imported = entries[1]
    assert json.loads(imported.data)["count"] == 2
    # The imported list itself never reaches the log.
    assert "example.org" not in imported.data and "Silva" not in imported.data
    assert "Festival 2019" in imported.display() and "hashes only" in imported.display()
    assert entries[0].display() == "Analytics series “Other Fest” was created."


def test_copied_event_joins_the_series_with_its_settings(make_edition, series, organizer):
    from pretix.base.models import Event

    from pretix_event_analytics.models import EventAnalyticsConfig

    kit = make_edition(2026)
    cfg = kit.event.analytics_config
    cfg.tracked_question_ids = [kit.diet_q.pk]
    cfg.ticket_target = 1500
    cfg.pace_alert_threshold = 15
    cfg.save()
    with scopes_disabled():
        new = Event.objects.create(organizer=organizer, name="Suti 2027", slug="suti-2027", currency="EUR",
                                   date_from=datetime.datetime(2027, 8, 26, 18, 0, tzinfo=datetime.timezone.utc))
        new.copy_data_from(kit.event)
        new_q = new.questions.get(identifier=kit.diet_q.identifier)
    copied = EventAnalyticsConfig.objects.get(event=new)
    assert copied.series == series and copied.edition_year == 2027 and copied.home_country == "PT"
    assert copied.tracked_question_ids == [new_q.pk]  # mapped to the new event's question
    assert copied.pace_alert_threshold == 15
    assert copied.ticket_target is None  # targets belong to one edition
