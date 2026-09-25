"""Section builders: arithmetic that the dashboards depend on."""
import datetime

import pytest

from conftest import make_team
from django.test import RequestFactory
from django.utils import timezone

from pretix_event_analytics.services.reports import loyalty, sales
from pretix_event_analytics.services.reports.scope import ReportScope
from pretix_event_analytics.services.resync_service import resync_series


@pytest.fixture(autouse=True)
def _organizer_scope(organizer, viewer):
    # Views run inside Pretix's scope middleware; mirror that here.
    from django_scopes import scope
    with scope(organizer=organizer):
        yield


@pytest.fixture
def viewer(organizer):
    from django_scopes import scopes_disabled
    from pretix.base.models import User
    user = User.objects.create_user("viewer@example.org", "x")
    with scopes_disabled():
        team = make_team(organizer, "Viewers", can_view_orders=True)
        team.members.add(user)
    return user


def _scope(kit, **params):
    from django.http import QueryDict
    from pretix.base.models import User
    req = RequestFactory().get("/", params)
    req.event = kit.event
    req.user = User.objects.get(email="viewer@example.org")
    from django.contrib.sessions.backends.db import SessionStore
    req.session = SessionStore()
    qd = QueryDict(mutable=True)
    for k, v in params.items():
        if isinstance(v, list):
            qd.setlist(k, v)
        else:
            qd[k] = v
    return ReportScope(req, kit.event, params=qd)


def test_heatmap_uses_event_timezone(make_edition, series):
    kit = make_edition(2026)
    order = kit.order("tz@example.org")
    # 23:30 UTC on a Monday is 00:30 Tuesday in Lisbon summer time (UTC+1)
    from django_scopes import scopes_disabled
    with scopes_disabled():
        order.datetime = datetime.datetime(2026, 6, 1, 23, 30, tzinfo=datetime.timezone.utc)
        order.save()
    resync_series(series)
    grid = sales.build(_scope(kit))["heatmap"]["rows"]
    tue = next(r for r in grid if r["day"] == "Tue")
    assert tue["cells"][0]["n"] == 1


def test_forecast_uses_previous_editions(make_edition, series, monkeypatch):
    now = timezone.now()
    past = make_edition(2025, date=now - datetime.timedelta(days=365))
    current = make_edition(2026, date=now + datetime.timedelta(days=30))
    # 2025: 2 tickets 60 days out, 2 more in the final month → grew ×2 from T-30
    past.order("a@example.org", days_before=60)
    past.order("b@example.org", days_before=40)
    past.order("c@example.org", days_before=10)
    past.order("d@example.org", days_before=5)
    # 2026: 3 tickets sold so far (all ≥30 days out)
    for i in range(3):
        current.order(f"n{i}@example.org", days_before=45)
    resync_series(series)
    f = sales.build(_scope(current))["forecast"]
    assert f is not None
    assert f["tickets"] == 6          # 3 × (4 / 2)
    assert f["basis"] == [2025]


def test_timing_buckets_cover_all_tickets(make_edition, series):
    kit = make_edition(2026)
    for d in (400, 120, 45, 20, 10, 2):
        kit.order(f"t{d}@example.org", days_before=d)
    resync_series(series)
    table = sales.build(_scope(kit))["timing"]["table"]
    assert sum(r["tickets"] for r in table) == 6


@pytest.mark.parametrize("unit", ["people", "buyers"])
def test_loyalty_frequency_counts_people_once(make_edition, series, unit):
    e1, e2, e3 = make_edition(2023), make_edition(2024), make_edition(2025)
    for kit in (e1, e2, e3):
        kit.order("always@example.org", [{"item": kit.ga, "attendee_name": "Alva Reis", "birth": "1990-01-01"}])
    e1.order("once@example.org", [{"item": e1.ga, "attendee_name": "Otto Lima", "birth": "1991-01-01"}])
    resync_series(series)
    data = loyalty.build(_scope(e3, unit=unit))
    freq = {r["label"]: r["count"] for r in data["frequency_table"]}
    assert freq == {"1 edition": 1, "2 editions": 0, "3 editions": 1}
    assert data["attended_all"] == 1
    assert data["focus"]["loyal_all"] == 1
