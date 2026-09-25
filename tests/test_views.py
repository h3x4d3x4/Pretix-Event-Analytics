"""Every control-panel page renders for a user with the right permissions."""
import pytest
from django.urls import reverse

from pretix_event_analytics.services.resync_service import resync_series


@pytest.fixture
def populated(make_edition, series):
    e24, e26 = make_edition(2024), make_edition(2026)
    for i in range(5):
        e24.order(f"p{i}@example.org", [{"item": e24.ga, "birth": "1990-05-01"}], country="PT")
    for i in range(3, 9):
        o = e26.order(f"p{i}@example.org", [{"item": e26.vip, "addons": [e26.parking]}], country="ES")
        if i % 2:
            e26.checkin(o)
    e26.order("refund@example.org", status="c", refund=True)
    resync_series(series)
    return e26


def _event_url(name, kit, **extra):
    return reverse(f"plugins:pretix_event_analytics:{name}",
                   kwargs={"organizer": kit.event.organizer.slug, "event": kit.event.slug, **extra})


EVENT_PAGES = ["dashboard", "config", "export_csv"]


@pytest.mark.parametrize("page", EVENT_PAGES)
def test_event_pages_render(admin_client, populated, page):
    r = admin_client.get(_event_url(page, populated))
    assert r.status_code == 200, r.content[:500]


def test_dashboard_with_filters(admin_client, populated):
    r = admin_client.get(_event_url("dashboard", populated), {"repeat_only": "on", "country": "ES"})
    assert r.status_code == 200


def test_series_list_renders(admin_client, populated):
    r = admin_client.get(reverse("plugins:pretix_event_analytics:series_list",
                                 kwargs={"organizer": populated.event.organizer.slug}))
    assert r.status_code == 200
