"""Every control-panel page renders and every option combination works."""
import json
import re

import pytest

from conftest import make_team
from django.urls import reverse
from django_scopes import scopes_disabled

from pretix_event_analytics.services.resync_service import resync_series


@pytest.fixture
def populated(make_edition, series):
    from pretix.base.models import ItemCategory, Voucher

    e23, e24, e26 = make_edition(2023), make_edition(2024), make_edition(2026)
    for i in range(6):
        e23.order(f"p{i}@example.org", [{"item": e23.ga, "birth": "1990-05-01"}], country="PT", days_before=100 - i)
    for i in range(3, 10):
        e24.order(f"p{i}@example.org", [{"item": e24.ga, "attendee_email": f"p{i}@example.org"}], country="ES",
                  days_before=40 + i)
    with scopes_disabled():
        cat = ItemCategory.objects.create(event=e26.event, name="Festival")
        e26.vip.category = cat
        e26.vip.save()
        v = Voucher.objects.create(event=e26.event, code="CREW26", tag="crew", item=e26.ga)
    cfg = e26.event.analytics_config
    cfg.tracked_question_ids = [e26.diet_q.pk]
    cfg.ticket_target = 50
    cfg.save()
    for i in range(0, 12, 2):
        o = e26.order(f"p{i}@example.org", [
            {"item": e26.vip, "addons": [e26.parking], "diet": e26.diet_veg, "birth": "1985-01-01"},
            {"item": e26.ga, "attendee_email": f"friend{i}@example.org", "diet": e26.diet_any},
        ], country="ES" if i % 4 else "PT", days_before=120 - i * 9, provider="stripe" if i % 3 else "banktransfer")
        if i % 4 == 0:
            e26.checkin(o)
    e26.order("crew@example.org", [{"item": e26.ga, "voucher": v, "price": 0}], days_before=3)
    e26.order("refund@example.org", status="c", refund=True, days_before=50)
    resync_series(series)
    return e26


def _url(name, kit, **extra):
    return reverse(f"plugins:pretix_event_analytics:{name}",
                   kwargs={"organizer": kit.event.organizer.slug, "event": kit.event.slug, **extra})


PAGES = ["dashboard", "sales", "audience", "loyalty", "tickets", "operations", "resale", "config"]


def _charts(response):
    m = re.search(rb'<script type="application/json" id="pa-charts"[^>]*>(.*?)</script>', response.content, re.S)
    return json.loads(m.group(1)) if m else {}


@pytest.mark.parametrize("page", PAGES)
def test_pages_render(admin_client, populated, page):
    r = admin_client.get(_url(page, populated))
    assert r.status_code == 200, r.content[:800]


@pytest.mark.parametrize("page", PAGES[:-1])
def test_pages_render_with_filters(admin_client, populated, page):
    params = {"country": "ES", "buyer_type": "returning", "include_refunded": "on",
              "date_from": "2026-01-01", "ticket_type": ["VIP"], "provider": "stripe"}
    r = admin_client.get(_url(page, populated), params)
    assert r.status_code == 200


@pytest.mark.parametrize("page", PAGES[:-1])
def test_pages_render_empty_event(admin_client, make_edition, page):
    kit = make_edition(2030, in_series=False)
    r = admin_client.get(_url(page, kit))
    assert r.status_code == 200


@pytest.mark.parametrize("granularity", ["day", "week", "month"])
@pytest.mark.parametrize("split", ["none", "product", "category", "buyer_type", "country"])
def test_sales_options(admin_client, populated, granularity, split):
    r = admin_client.get(_url("sales", populated), {"granularity": granularity, "split": split, "basis": "payment"})
    assert r.status_code == 200
    charts = _charts(r)
    tickets = next(c for k, c in charts.items() if k.endswith("tickets_chart"))
    revenue = next(c for k, c in charts.items() if k.endswith("revenue_chart"))
    # Tickets chart counts admissions only; revenue matches order totals when not split by product.
    assert sum(sum(s["data"]) for s in tickets["series"]) == 12 + 1
    if split not in ("product", "category"):
        assert round(sum(sum(s["data"]) for s in revenue["series"]), 2) == 6 * (200 + 20 + 100)


def test_sales_never_uses_dual_axis(admin_client, populated):
    charts = _charts(admin_client.get(_url("sales", populated)))
    for c in charts.values():
        assert "y1" not in json.dumps(c)


def test_editions_compared(admin_client, populated):
    r = admin_client.get(_url("sales", populated))
    charts = _charts(r)
    pace = next(c for k, c in charts.items() if k.endswith("pacing_tickets"))
    assert [s["name"] for s in pace["series"]] == ["2023", "2024", "2026"]
    assert pace["highlight"] == 2


@pytest.mark.parametrize("unit", ["people", "buyers"])
def test_loyalty_units_and_compare(admin_client, populated, unit):
    r = admin_client.get(_url("loyalty", populated), {"unit": unit})
    assert r.status_code == 200
    keys = [e["key"] for e in r.context["data"]["editions"]]
    r2 = admin_client.get(_url("loyalty", populated), {"unit": unit, "compare": keys[:2]})
    assert r2.status_code == 200
    assert r2.context["data"]["selected"] == keys[:2]


def test_loyalty_numbers(admin_client, populated):
    data = admin_client.get(_url("loyalty", populated), {"unit": "buyers"}).context["data"]
    focus = data["focus"]
    # 2026 buyers: p0,p2,p4,p6,p8,p10 + crew; p0,p2,p4 were in 2023; p4,p6,p8 in 2024.
    assert focus["participants"] == 7
    assert focus["returning"] == 5            # p0 p2 p4 p6 p8
    assert focus["from_last"] == 3            # p4 p6 p8 (2024)
    assert focus["after_gap"] == 2            # p0 p2 (2023 only)
    freq = {r["label"]: r["count"] for r in data["frequency_table"]}
    assert freq["3 editions"] == 1            # p4


@pytest.mark.parametrize("kind", ["csv", "tickets", "loyalty"])
def test_csv_exports(admin_client, populated, kind):
    r = admin_client.get(_url("export", populated, kind=kind))
    assert r.status_code == 200
    body = b"".join(r.streaming_content).decode()
    assert len(body.strip().splitlines()) > 5
    assert "@example.org" not in body  # never leak e-mails


def test_pdf_export(admin_client, populated):
    pytest.importorskip("weasyprint")
    r = admin_client.get(_url("export", populated, kind="pdf"))
    assert r.status_code == 200
    assert r.content[:4] == b"%PDF"


def test_pretix_exporter_registered(populated):
    from pretix.base.signals import register_data_exporters

    from pretix_event_analytics.exporters import AnalyticsOrderExporter
    with scopes_disabled():
        classes = [resp for _r, resp in register_data_exporters.send(populated.event)]
        assert AnalyticsOrderExporter in classes
        exp = AnalyticsOrderExporter(populated.event, populated.event.organizer)
        name, ctype, data = exp.render({"_format": "default"})
    assert ctype.startswith("text/csv") and b"order_code" in data


def test_series_pages(admin_client, populated, series):
    org = populated.event.organizer.slug
    r = admin_client.get(reverse("plugins:pretix_event_analytics:series_list", kwargs={"organizer": org}))
    assert r.status_code == 200
    r = admin_client.get(reverse("plugins:pretix_event_analytics:series_detail", kwargs={"organizer": org, "pk": series.pk}))
    assert r.status_code == 200
    assert len(r.context["data"]["rows"]) == 3


def test_legacy_import_and_delete(admin_client, populated, series):
    from pretix_event_analytics.models import AnalyticsOrderFact, LegacyEdition
    org = populated.event.organizer.slug
    url = reverse("plugins:pretix_event_analytics:legacy_import", kwargs={"organizer": org, "pk": series.pk})
    r = admin_client.post(url, {"label": "Suti 2019", "edition_year": 2019,
                                "emails_text": "name;email\nX;p10@example.org\nY;nobody@example.org"})
    assert r.status_code == 302
    le = LegacyEdition.objects.get(series=series)
    assert le.identities.count() == 2
    fact = AnalyticsOrderFact.objects.get(event=populated.event, repeat_hash__isnull=False, order_code__in=[
        f.order_code for f in AnalyticsOrderFact.objects.filter(event=populated.event, first_seen_edition_year=2019)])
    assert fact.is_repeat_buyer
    admin_client.post(reverse("plugins:pretix_event_analytics:legacy_delete",
                              kwargs={"organizer": org, "pk": series.pk, "legacy_pk": le.pk}))
    fact.refresh_from_db()
    assert not fact.is_repeat_buyer


def test_config_saves_questions_and_targets(admin_client, populated):
    kit = populated
    r = admin_client.post(_url("config", kit), {
        "series": kit.event.analytics_config.series_id, "edition_year": 2026, "home_country": "PT",
        "is_active": "on", "ticket_target": 100, "revenue_target": "", "tracked_questions": [str(kit.diet_q.pk)],
    })
    assert r.status_code == 302
    kit.event.analytics_config.refresh_from_db()
    assert kit.event.analytics_config.ticket_target == 100
    assert kit.event.analytics_config.tracked_question_ids == [kit.diet_q.pk]


def test_questions_are_aggregated(admin_client, populated):
    data = admin_client.get(_url("operations", populated)).context["data"]
    q = data["questions"][0]
    answers = {r["answer"]: r["count"] for r in q["rows"]}
    assert answers == {"Vegetarian": 6, "Anything": 6}


def test_chart_json_is_script_safe(admin_client, make_edition, series):
    kit = make_edition(2026)
    with scopes_disabled():
        kit.ga.name = "</script><script>alert(1)</script>"
        kit.ga.save()
    kit.order("x@example.org")
    resync_series(series)
    r = admin_client.get(_url("tickets", kit))
    assert b"</script><script>alert(1)" not in r.content


def test_nav_has_children(admin_client, populated):
    r = admin_client.get(_url("sales", populated))
    assert _url("loyalty", populated).encode() in r.content


def test_permission_required(client, populated):
    from pretix.base.models import User
    User.objects.create_user("nobody@example.org", "pw")
    client.login(email="nobody@example.org", password="pw")
    r = client.get(_url("dashboard", populated))
    assert r.status_code in (302, 403, 404)


@pytest.fixture
def limited_client(client, populated):
    """A team member who may view orders of the 2026 edition only."""
    from pretix.base.models import User
    user = User.objects.create_user("limited@example.org", "pw")
    with scopes_disabled():
        team = make_team(populated.event.organizer, "2026 only", all_events=False, can_view_orders=True)
        team.limit_events.add(populated.event)
        team.members.add(user)
    client.login(email="limited@example.org", password="pw")
    return client


def test_editions_filter_cannot_read_other_events(limited_client, populated):
    from pretix.base.models import Event
    with scopes_disabled():
        other = Event.objects.get(slug="suti-2023")
    r = limited_client.get(_url("export", populated, kind="csv"), {"editions": [str(other.pk)]})
    body = b"".join(r.streaming_content).decode()
    assert "suti-2023" not in body
    r = limited_client.get(_url("sales", populated), {"editions": [str(other.pk)]})
    assert r.context["scope"].event_ids == [populated.event.pk]
    pace = [c for k, c in _charts(r).items() if k.endswith("pacing_tickets")]
    assert all(s["name"] in ("2026", "Suti 2026") for c in pace for s in c["series"])


def test_series_wide_views_need_access_to_all_editions(limited_client, populated, series):
    r = limited_client.get(_url("loyalty", populated))
    assert r.context["data"].get("no_permission") is True
    r = limited_client.get(_url("dashboard", populated))
    assert "first_timers" not in r.context["data"]
    org = populated.event.organizer.slug
    r = limited_client.get(reverse("plugins:pretix_event_analytics:series_detail", kwargs={"organizer": org, "pk": series.pk}))
    assert r.status_code in (302, 403, 404)


def test_csv_formula_injection_is_neutralised(admin_client, make_edition, series):
    from pretix.base.models import InvoiceAddress
    kit = make_edition(2026)
    o = kit.order("x@example.org")
    with scopes_disabled():
        InvoiceAddress.objects.filter(order=o).update(city='=HYPERLINK("http://evil","x")')
    resync_series(series)
    body = b"".join(admin_client.get(_url("export", kit, kind="csv")).streaming_content).decode()
    assert "'=HYPERLINK" in body
