"""Resale: TicketSwap + manual name changes, one total, counted once per ticket."""
import json

from django_scopes import scopes_disabled

from pretix_event_analytics.services.resale import _is_real_name_change, resale_stats
from pretix_event_analytics.services.resync_service import resync_event


def _positions(order):
    with scopes_disabled():
        return list(order.positions.filter(addon_to__isnull=True).order_by("positionid"))


def _swap(position, count=1, personalized=False):
    with scopes_disabled():
        position.meta_info = json.dumps({"ticketswap": {"swap_count": count, "personalized": personalized,
                                                        "customer": {"firstName": "never read"}}})
        position.save(update_fields=["meta_info"])


def _rename(order, items, admin=True):
    """Log exactly like Pretix: admin edits carry the position id, customer edits do not."""
    with scopes_disabled():
        data = [dict(position=p.pk, **changes) if admin else changes for p, changes in items]
        order.log_action("pretix.event.order.modified", {"data": data})


def _name(full):
    return {"attendee_name_parts": {"_scheme": "full", "full_name": full}}


def test_name_change_heuristic():
    assert _is_real_name_change("John Doe", "Jane Smith")
    assert not _is_real_name_change("John Doe", "John Doa")
    assert not _is_real_name_change("John Doe", "John M Doe")


def test_channels_are_combined_and_counted_once(make_edition):
    kit = make_edition(2026)
    with scopes_disabled():
        kit.event.plugins += ",pretix_ticketswap"
        kit.event.save()
    o1 = kit.order("a@example.org", [{"item": kit.ga}, {"item": kit.ga}])
    o2 = kit.order("b@example.org")
    kit.order("c@example.org")
    p1a, p1b = _positions(o1)
    (p2,) = _positions(o2)
    _swap(p1a, count=2, personalized=True)                         # TicketSwap, resold twice
    _rename(o1, [(p1a, {**_name("New Holder"), "question_%d" % kit.birth_q.pk: "1990-01-01"})])  # both channels
    _rename(o2, [(p2, {**_name("Someone Else"), "attendee_email": "x@example.org"})], admin=False)  # manual
    resync_event(kit.event)

    s = resale_stats(kit.event)
    assert s["tickets"] == 4 and s["changed"] == 2 and s["rate"] == 50.0
    assert s["channels"] == {"ticketswap": 0, "manual": 1, "both": 1}
    assert s["chains"] == 1 and s["personalized"] == 1 and s["ticketswap_active"]
    assert p1b.pk not in {t.position_id for t in s["rows"]}


def test_name_only_edit_is_unclear_not_resale(make_edition):
    kit = make_edition(2026)
    o = kit.order("a@example.org")
    (p,) = _positions(o)
    _rename(o, [(p, _name("Jon Smith"))])                     # previous name unknown → unclear
    _rename(o, [(p, _name("John Smith"))])                    # typo fix of the logged name → ignored
    resync_event(kit.event)
    s = resale_stats(kit.event)
    assert s["changed"] == 0 and s["unclear"] == 1
    _rename(o, [(p, _name("Maria Fernandes"))])               # clearly different from the logged name
    s = resale_stats(kit.event)
    assert s["changed"] == 1 and s["channels"]["manual"] == 1 and s["unclear"] == 0


def test_refunded_orders_are_not_counted(make_edition):
    kit = make_edition(2026)
    o = kit.order("a@example.org", status="c", refund=True)
    with scopes_disabled():
        (p,) = list(o.all_positions.all())
    _swap(p)
    resync_event(kit.event)
    assert resale_stats(kit.event)["changed"] == 0


def test_resale_page_and_csv(admin_client, make_edition):
    from django.urls import reverse
    kit = make_edition(2026)
    o = kit.order("a@example.org")
    _swap(_positions(o)[0])
    resync_event(kit.event)
    kw = {"organizer": kit.event.organizer.slug, "event": kit.event.slug}
    page = admin_client.get(reverse("plugins:pretix_event_analytics:resale", kwargs=kw))
    assert page.status_code == 200 and b"100.0%" in page.content
    csv_body = b"".join(admin_client.get(reverse("plugins:pretix_event_analytics:export",
                                                 kwargs={**kw, "kind": "resale"})).streaming_content).decode()
    assert o.code in csv_body and "ticketswap" in csv_body and "never read" not in csv_body
