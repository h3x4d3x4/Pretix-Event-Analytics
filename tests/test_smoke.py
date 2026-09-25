from pretix_event_analytics.models import AnalyticsOrderFact


def test_ingest_single_order(make_edition, ingest):
    kit = make_edition(2026)
    order = kit.order("a@example.org")
    ingest(order)
    fact = AnalyticsOrderFact.objects.get(order_code=order.code)
    assert fact.ticket_count == 1
    assert fact.total_gross == 100


def test_salt_from_pretix_cfg(settings, monkeypatch):
    import configparser

    from pretix.helpers.config import EnvOrParserConfig

    from pretix_event_analytics.services import hash_service

    settings.PRETIX_ANALYTICS_SECRET_SALT = None
    settings.DEBUG = False
    cp = configparser.RawConfigParser()
    cp.read_string("[pretix_event_analytics]\nsecret_salt = from-the-config-file-123456\n")
    monkeypatch.setattr(settings, "CONFIG_FILE", EnvOrParserConfig(cp), raising=False)
    assert hash_service._get_salt() == "from-the-config-file-123456"


def test_salt_missing_fails_closed(settings, monkeypatch):
    import configparser

    import pytest
    from django.core.exceptions import ImproperlyConfigured
    from pretix.helpers.config import EnvOrParserConfig

    from pretix_event_analytics.services import hash_service

    settings.PRETIX_ANALYTICS_SECRET_SALT = None
    settings.DEBUG = False
    monkeypatch.setattr(settings, "CONFIG_FILE", EnvOrParserConfig(configparser.RawConfigParser()), raising=False)
    monkeypatch.delenv("PRETIX_ANALYTICS_SECRET_SALT", raising=False)
    with pytest.raises(ImproperlyConfigured):
        hash_service._get_salt()


def test_generate_test_data_command(make_edition, series):
    from django.core.management import call_command

    from pretix_event_analytics.models import AnalyticsOrderFact

    e24, e26 = make_edition(2024), make_edition(2026)
    for kit, year in ((e24, 2024), (e26, 2026)):
        call_command("generate_test_data", "--organizer", "suti", "--event", kit.event.slug,
                     "--series", series.slug, "--edition-year", str(year), "--orders", "40",
                     "--i-understand-this-is-fake-data")
    facts = AnalyticsOrderFact.objects.filter(event=e26.event)
    assert facts.count() == 40
    assert facts.filter(is_repeat_buyer=True).exists()
