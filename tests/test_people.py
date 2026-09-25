"""Series-wide identity resolution and attendance."""
from pretix_event_analytics.models import AnalyticsOrderFact, LegacyEdition, LegacyIdentity
from pretix_event_analytics.services.attendance import load_attendance
from pretix_event_analytics.services.cohort_service import build_cohort_matrix, get_cohort_sizes
from pretix_event_analytics.services.hash_service import generate_repeat_hash
from pretix_event_analytics.services.people import recompute_series
from pretix_event_analytics.services.resync_service import resync_event, resync_series


def _fact(order):
    return AnalyticsOrderFact.objects.get(event=order.event, order_code=order.code)


def test_person_keys_are_stable_across_runs(make_edition, series):
    e24 = make_edition(2024)
    o = e24.order("stable@example.org")
    resync_series(series)
    first = _fact(o).person_key
    resync_series(series)
    assert first and _fact(o).person_key == first


def test_repeat_from_last_edition_and_gaps(make_edition, series):
    e23, e24, e25 = make_edition(2023), make_edition(2024), make_edition(2025)
    e23.order("gap@example.org")
    e24.order("steady@example.org")
    o_gap = e25.order("gap@example.org")
    o_steady = e25.order("steady@example.org")
    resync_series(series)
    gap, steady = _fact(o_gap), _fact(o_steady)
    assert gap.is_repeat_buyer and not gap.repeat_from_last_edition
    assert steady.is_repeat_buyer and steady.repeat_from_last_edition
    assert gap.first_seen_edition_year == 2023


def test_inactive_edition_is_ignored(make_edition, series):
    e24, e26 = make_edition(2024), make_edition(2026)
    e24.order("a@example.org")
    o = e26.order("a@example.org")
    cfg = e24.event.analytics_config
    cfg.is_active = False
    cfg.save()
    resync_series(series)
    assert _fact(o).is_repeat_buyer is False


def test_legacy_list_marks_returning(make_edition, series):
    e26 = make_edition(2026)
    legacy = LegacyEdition.objects.create(series=series, label="Suti 2019", edition_year=2019)
    LegacyIdentity.objects.create(legacy_edition=legacy, identity_hash=generate_repeat_hash("old@example.org"))
    o = e26.order("old@example.org")
    fresh = e26.order("new@example.org")
    resync_series(series)
    assert _fact(o).is_repeat_buyer and _fact(o).first_seen_edition_year == 2019
    assert not _fact(fresh).is_repeat_buyer


def test_payment_fingerprint_links_different_emails(make_edition, series):
    e24, e26 = make_edition(2024), make_edition(2026)
    card = {"payment_method_details": {"card": {"fingerprint": "fp_123", "country": "PT"}}}
    e24.order("work@example.org", provider="stripe", payment_info=card)
    o = e26.order("home@example.org", provider="stripe", payment_info=card)
    resync_series(series)
    assert _fact(o).is_repeat_buyer


def test_attendance_and_cohort_use_people(make_edition, series):
    e24, e26 = make_edition(2024), make_edition(2026)
    # 2024: one buyer with two attendees; 2026: one of the attendees returns
    e24.order("buyer@example.org", [
        {"item": e24.ga, "attendee_email": "p1@example.org"},
        {"item": e24.ga, "attendee_email": "p2@example.org"},
    ])
    e26.order("p1@example.org")
    resync_series(series)

    people = load_attendance(series.organizer_id, series.slug, "people")
    sizes = {e.year: len(people.sets[e.key]) for e in people.editions}
    assert sizes == {2024: 3, 2026: 1}  # buyer + 2 attendees; p1
    assert build_cohort_matrix(series.slug, series.organizer_id) == {2024: {2026: round(1 / 3, 4)}, 2026: {}}

    buyers = load_attendance(series.organizer_id, series.slug, "buyers")
    assert {e.year: len(buyers.sets[e.key]) for e in buyers.editions} == {2024: 1, 2026: 1}
    assert get_cohort_sizes(series.slug, series.organizer_id, "buyers") == {2024: 1, 2026: 1}


def test_unidentified_tickets_are_counted_separately(make_edition, series):
    e26 = make_edition(2026)
    e26.order("buyer@example.org", [{"item": e26.ga}, {"item": e26.ga}, {"item": e26.ga}])
    resync_series(series)
    att = load_attendance(series.organizer_id, series.slug, "people")
    key = att.editions[0].key
    assert len(att.sets[key]) == 1          # the buyer
    assert att.unidentified[key] == 3       # three anonymous group tickets


def test_live_ingestion_with_worker_resolves(make_edition, ingest, settings):
    settings.HAS_CELERY = True  # tasks still run eagerly in tests
    e24, e26 = make_edition(2024), make_edition(2026)
    ingest(e24.order("live@example.org"))
    o = e26.order("live@example.org")
    ingest(o)
    f = _fact(o)
    assert f.is_repeat_buyer and f.person_key


def test_without_worker_periodic_task_resolves(make_edition, ingest, settings):
    from pretix.base.signals import periodic_task

    settings.HAS_CELERY = False
    e24, e26 = make_edition(2024), make_edition(2026)
    ingest(e24.order("cron@example.org"))
    o = e26.order("cron@example.org")
    ingest(o)
    f = _fact(o)
    assert f.is_repeat_buyer          # immediate backward-looking status
    assert f.person_key == ""         # full resolution not run in the request
    periodic_task.send(sender=None)
    f = _fact(o)
    assert f.person_key and f.repeat_count == 1
    # Nothing changed since: a second run is a no-op.
    from pretix_event_analytics.services.people import resolve_dirty_scopes
    assert resolve_dirty_scopes() == 0


def test_shared_payment_account_does_not_merge_people(make_edition, series):
    e24, e26 = make_edition(2024), make_edition(2026)
    agency = {"iban": "PT50000000000000000000001"}
    for i in range(6):
        e24.order(f"client{i}@example.org", provider="banktransfer", payment_info=agency)
    o = e26.order("someone-new@example.org", provider="banktransfer", payment_info=agency)
    resync_series(series)
    assert _fact(o).is_repeat_buyer is False


def test_legacy_only_series_gets_person_keys(series):
    from pretix_event_analytics.services.people import run_scope
    le = LegacyEdition.objects.create(series=series, label="Suti 2019", edition_year=2019)
    LegacyIdentity.objects.create(legacy_edition=le, identity_hash=generate_repeat_hash("a@example.org"))
    run_scope(series.organizer_id, series.slug)
    assert LegacyIdentity.objects.get(legacy_edition=le).person_key


def test_refunded_order_does_not_count_its_own_edition(make_edition, series):
    e26 = make_edition(2026)
    o = e26.order("gone@example.org", status="c", refund=True)
    resync_series(series)
    assert _fact(o).editions_attended == 1  # never 2 from double counting


def test_config_edition_year_change_propagates(make_edition, series):
    e = make_edition(2024)
    o = e.order("x@example.org")
    resync_event(e.event)
    cfg = e.event.analytics_config
    cfg.edition_year = 2023
    cfg.save()
    recompute_series(series.organizer_id, series.slug)
    assert _fact(o).edition_year == 2023
