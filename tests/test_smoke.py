from pretix_event_analytics.models import AnalyticsOrderFact


def test_ingest_single_order(make_edition, ingest):
    kit = make_edition(2026)
    order = kit.order("a@example.org")
    ingest(order)
    fact = AnalyticsOrderFact.objects.get(order_code=order.code)
    assert fact.ticket_count == 1
    assert fact.total_gross == 100
