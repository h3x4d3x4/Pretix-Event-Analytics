"""
Regression tests for the correctness issues found in the 2026-09 audit.
"""
from decimal import Decimal

from pretix_event_analytics.models import AnalyticsOrderFact, AnalyticsTicketFact
from pretix_event_analytics.services.people import recompute_series
from pretix_event_analytics.services.resync_service import resync_event


def _fact(order):
    return AnalyticsOrderFact.objects.get(event=order.event, order_code=order.code)


# ── Ticket facts ──────────────────────────────────────────────────────────────

def test_same_product_twice_keeps_every_ticket_live(make_edition, ingest):
    kit = make_edition(2026)
    order = kit.order("group@example.org", [{"item": kit.ga}, {"item": kit.ga}, {"item": kit.ga}])
    ingest(order)
    assert AnalyticsTicketFact.objects.filter(order_fact=_fact(order)).count() == 3


def test_same_product_twice_survives_resync(make_edition):
    kit = make_edition(2026)
    order = kit.order("group@example.org", [{"item": kit.ga}, {"item": kit.ga}])
    result = resync_event(kit.event)
    assert result["skipped"] == 0
    assert AnalyticsTicketFact.objects.filter(order_fact=_fact(order)).count() == 2


def test_net_and_fees(make_edition, ingest):
    kit = make_edition(2026)
    order = kit.order("fees@example.org", fees=Decimal("2.50"))
    ingest(order)
    fact = _fact(order)
    assert fact.total_gross == Decimal("102.50")
    assert fact.fees_total == Decimal("2.50")
    # No tax rules configured → net == gross, tax == 0
    assert fact.tax_amount == Decimal("0.00")


# ── Resync ────────────────────────────────────────────────────────────────────

def test_resync_keeps_canceled_and_refunded_orders(make_edition):
    kit = make_edition(2026)
    kit.order("paid@example.org")
    refunded = kit.order("refund@example.org", status="c", refund=True)
    resync_event(kit.event)
    fact = _fact(refunded)
    assert fact.order_status == "c"
    assert fact.is_refunded is True
    assert fact.canceled_at is not None


def test_resync_does_not_empty_table_on_failure(make_edition, ingest, monkeypatch):
    kit = make_edition(2026)
    order = kit.order("a@example.org")
    ingest(order)

    from pretix_event_analytics.services import resync_service

    def boom(*a, **kw):
        raise RuntimeError("upstream exploded")
    monkeypatch.setattr(resync_service, "_load_order_pks", boom)
    try:
        resync_event(kit.event)
    except RuntimeError:
        pass
    assert AnalyticsOrderFact.objects.filter(event=kit.event).count() == 1


# ── Repeat detection ──────────────────────────────────────────────────────────

def test_repeat_status_independent_of_sync_order(make_edition):
    e24 = make_edition(2024)
    e26 = make_edition(2026)
    e24.order("loyal@example.org")
    o26 = e26.order("loyal@example.org")
    # Sync the NEWER edition first — historically this marked everyone new.
    resync_event(e26.event)
    resync_event(e24.event)
    fact = _fact(o26)
    assert fact.is_repeat_buyer is True
    assert fact.repeat_count == 1
    assert fact.first_seen_edition_year == 2024


def test_guest_who_later_buys_is_a_returning_person_not_a_returning_buyer(make_edition):
    e24 = make_edition(2024)
    e26 = make_edition(2026)
    # 2024: a friend bought the ticket for Sam
    e24.order("friend@example.org", [{"item": e24.ga, "attendee_name": "Sam Costa", "birth": "1995-03-02"}])
    # 2026: Sam buys for themself
    o26 = e26.order("sam@example.org", [{"item": e26.ga, "attendee_name": "Sam Costa", "birth": "1995-03-02"}])
    resync_event(e24.event)
    resync_event(e26.event)
    assert _fact(o26).is_repeat_buyer is False  # new customer (e-mail)
    ticket = AnalyticsTicketFact.objects.get(order_fact=_fact(o26))
    assert ticket.is_returning_attendee is True  # but has been before
    assert ticket.attendee_match == "certain"


def test_group_order_does_not_merge_attendees(make_edition):
    e24 = make_edition(2024)
    e26 = make_edition(2026)
    e24.order("organiser@example.org", [
        {"item": e24.ga, "attendee_name": "Xavier Lopes", "birth": "1990-01-10"},
        {"item": e24.ga, "attendee_name": "Yara Nunes", "birth": "1992-05-20"},
    ])
    # Only Xavier returns in 2026, next to a stranger
    e26.order("x@example.org", [{"item": e26.ga, "attendee_name": "Xavier Lopes", "birth": "1990-01-10"}])
    e26.order("z@example.org", [{"item": e26.ga, "attendee_name": "Zé Pinto", "birth": "1991-07-07"}])
    resync_event(e24.event)
    resync_event(e26.event)
    recompute_series(e26.event.organizer_id, "suti-festival")
    tickets_26 = AnalyticsTicketFact.objects.filter(event=e26.event, is_addon=False)
    assert sorted(t.is_returning_attendee for t in tickets_26) == [False, True]


def test_refunded_previous_edition_does_not_count(make_edition):
    e24 = make_edition(2024)
    e26 = make_edition(2026)
    e24.order("gone@example.org", status="c", refund=True)
    o26 = e26.order("gone@example.org")
    resync_event(e24.event)
    resync_event(e26.event)
    assert _fact(o26).is_repeat_buyer is False


# ── Scoring ───────────────────────────────────────────────────────────────────

def test_checkin_does_not_drop_early_bird_points(make_edition, ingest):
    kit = make_edition(2026)
    order = kit.order("early@example.org", days_before=90)
    ingest(order)
    before = _fact(order).predicted_repeat_probability
    kit.checkin(order)
    after = _fact(order).predicted_repeat_probability
    assert after == before + 20
