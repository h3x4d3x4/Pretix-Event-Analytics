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


def test_attendee_email_counts_as_returning(make_edition):
    e24 = make_edition(2024)
    e26 = make_edition(2026)
    # 2024: a friend bought the ticket for "sam"
    e24.order("friend@example.org", [{"item": e24.ga, "attendee_email": "sam@example.org"}])
    # 2026: sam buys for themself
    o26 = e26.order("sam@example.org")
    resync_event(e24.event)
    resync_event(e26.event)
    assert _fact(o26).is_repeat_buyer is True


def test_group_order_does_not_merge_attendees(make_edition):
    e24 = make_edition(2024)
    e26 = make_edition(2026)
    e24.order("organiser@example.org", [
        {"item": e24.ga, "attendee_email": "x@example.org"},
        {"item": e24.ga, "attendee_email": "y@example.org"},
    ])
    # Only x returns in 2026
    e26.order("x@example.org")
    ox = e26.order("z@example.org")  # stranger
    resync_event(e24.event)
    resync_event(e26.event)
    recompute_series(e26.event.organizer_id, "suti-festival")
    assert _fact(ox).is_repeat_buyer is False
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
