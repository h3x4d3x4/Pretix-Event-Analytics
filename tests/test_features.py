"""Win-back lists, annotations, capacity, payment completion, cities, widgets."""
import csv
import datetime
import io

import pytest
from django.urls import reverse
from django_scopes import scopes_disabled

from pretix_event_analytics.models import SalesAnnotation
from pretix_event_analytics.services.resync_service import resync_series


def _url(name, kit, **extra):
    return reverse(f"plugins:pretix_event_analytics:{name}",
                   kwargs={"organizer": kit.event.organizer.slug, "event": kit.event.slug, **extra})


def _csv(response):
    return list(csv.reader(io.StringIO(b"".join(response.streaming_content).decode())))


def _t(kit, name, birth, email=None):
    return [{"item": kit.ga, "attendee_name": name, "birth": birth, "attendee_email": email}]


@pytest.fixture
def three_editions(make_edition, series):
    e23, e24, e25 = make_edition(2023), make_edition(2024), make_edition(2025)
    e23.order("lapsed@example.org", _t(e23, "Lara Pinto", "1990-01-01"), code="L23")
    e23.order("loyal@example.org", _t(e23, "Luis Moura", "1985-05-05"), code="Y23")
    e24.order("loyal@example.org", _t(e24, "Luis Moura", "1985-05-05"), code="Y24")
    e24.order("gone@example.org", _t(e24, "Gil Santos", "1992-02-02"), code="G24")
    e24.order("buyer@example.org", _t(e24, "Bruno Alves", "1980-03-03", "buyer@example.org")
              + _t(e24, "Gina Rocha", "1981-04-04"), code="B24")
    e25.order("loyal@example.org", _t(e25, "Luis Moura", "1985-05-05"), code="Y25")
    resync_series(series)
    return e25


def test_winback_list(admin_client, three_editions):
    rows = _csv(admin_client.get(_url("export", three_editions, kind="winback")))
    assert rows[0][0] == "last_order_code"
    body = rows[1:]
    codes = sorted(r[0] for r in body)
    # gone + buyer + guest (guest held a ticket in buyer's order) — loyal came back
    assert codes == ["B24", "B24", "G24"]
    roles = {r[2] for r in body if r[0] == "B24"}
    assert roles == {"buyer", "ticket holder"}
    assert "@" not in "".join(",".join(r) for r in rows)


def test_lapsed_list(admin_client, three_editions):
    body = _csv(admin_client.get(_url("export", three_editions, kind="lapsed")))[1:]
    assert [r[0] for r in body] == ["L23"]


def test_loyalty_page_offers_winback(admin_client, three_editions):
    fo = admin_client.get(_url("loyalty", three_editions)).context["data"]["focus"]
    assert fo["lost"] == 3 and fo["lapsed"] == 1


def test_annotations_add_delete_and_markers(admin_client, make_edition, series):
    kit = make_edition(2026)
    o = kit.order("a@example.org", days_before=40)
    resync_series(series)
    day = (kit.event.date_from - datetime.timedelta(days=40)).date()
    r = admin_client.post(_url("annotations", kit), {"date": day.isoformat(), "label": "Line-up announced"})
    assert r.status_code == 302
    ann = SalesAnnotation.objects.get(event=kit.event)
    ctx = admin_client.get(_url("sales", kit)).context
    markers = ctx["data"]["tickets_chart"].get("markers", [])
    assert any(m.get("kind") == "note" and "Line-up" in m["text"] for m in markers)
    pace = ctx["data"]["pacing_tickets"].get("markers", [])
    assert any(m["x"] == -40 for m in pace)
    admin_client.post(_url("annotations", kit), {"delete": ann.pk})
    assert not SalesAnnotation.objects.filter(event=kit.event).exists()
    assert o  # silence unused


def test_annotation_requires_settings_permission(client, make_edition, organizer):
    from conftest import make_team
    from pretix.base.models import User
    kit = make_edition(2026)
    user = User.objects.create_user("viewer@example.org", "pw")
    with scopes_disabled():
        make_team(organizer, "Viewers", can_view_orders=True).members.add(user)
    client.login(email="viewer@example.org", password="pw")
    r = client.post(_url("annotations", kit), {"date": "2026-01-01", "label": "x"})
    assert r.status_code in (302, 403, 404)
    assert not SalesAnnotation.objects.exists()


def test_capacity_and_payment_completion(admin_client, make_edition, series):
    kit = make_edition(2026)
    with scopes_disabled():
        q = kit.event.quotas.create(name="Festival", size=10)
        q.items.add(kit.ga)
    kit.order("paid@example.org")
    kit.order("never@example.org", status="e", provider="banktransfer")
    with scopes_disabled():
        from pretix.base.models import Order
        expired = Order.objects.get(email="never@example.org")
        expired.payments.create(provider="banktransfer", amount=100, state="created")
    resync_series(series)
    tickets = admin_client.get(_url("tickets", kit)).context["data"]
    cap = tickets["capacity"][0]
    assert (cap["size"], cap["paid"]) == (10, 1)
    funnel = admin_client.get(_url("operations", kit)).context["data"]["funnel"]
    bank = next(r for r in funnel["rows"] if "Bank" in r["label"])
    assert bank["paid"] == 1 and bank["expired"] == 1 and bank["conversion"] == 50.0


def test_cities(admin_client, make_edition, series):
    from pretix.base.models import InvoiceAddress
    kit = make_edition(2026)
    for i, city in enumerate(["Lisboa", "lisboa ", "Porto"]):
        o = kit.order(f"c{i}@example.org")
        with scopes_disabled():
            InvoiceAddress.objects.filter(order=o).update(city=city)
    resync_series(series)
    cities = admin_client.get(_url("audience", kit)).context["data"]["cities"]
    assert [(c["name"], c["orders"]) for c in cities] == [("Lisboa", 2), ("Porto", 1)]


def test_date_presets(admin_client, make_edition):
    kit = make_edition(2026)
    presets = admin_client.get(_url("sales", kit), {"split": "product"}).context["date_presets"]
    assert len(presets) == 4 and presets[-1]["active"]
    assert "split=product" in presets[0]["qs"] and "date_from=" in presets[0]["qs"]


def test_dashboard_widgets(make_edition, series):
    from pretix.control.signals import event_dashboard_widgets
    e24, e26 = make_edition(2024), make_edition(2026)
    e24.order("back@example.org")
    e26.order("back@example.org")
    e26.order("new@example.org")
    resync_series(series)
    with scopes_disabled():
        widgets = [w for _r, ws in event_dashboard_widgets.send(e26.event, subevent=None, lazy=False)
                   for w in ws if str(w.get("lazy", "")).startswith("analytics-")]
    from django.utils.safestring import SafeString
    assert widgets and all(isinstance(w["content"], SafeString) for w in widgets)  # else Pretix 2026.7 escapes the HTML
    contents = " ".join(w["content"] for w in widgets)
    assert "50%" in contents and "Returning buyers" in contents
