"""Card/payer details are read from the shapes Pretix really stores."""
from pretix_event_analytics.models import AnalyticsOrderFact
from pretix_event_analytics.services.payment_info import payment_country, stripe_card
from pretix_event_analytics.services.resync_service import resync_series

PAYMENT_INTENT = {  # structure of pretix's stripe info_data (PaymentIntent with charges)
    "object": "payment_intent",
    "payment_method_options": {"card": {"request_three_d_secure": "automatic"}},
    "charges": {"data": [
        {"status": "failed", "payment_method_details": {"card": {"fingerprint": "fp_old", "country": "US"}}},
        {"status": "succeeded", "payment_method_details": {"card": {"fingerprint": "fp_ok", "country": "ES", "brand": "visa"}}},
    ]},
}
LATEST_CHARGE = {"object": "payment_intent", "latest_charge": {"payment_method_details": {"card": {"fingerprint": "fp_lc", "country": "DE"}}}}
LEGACY_SOURCE = {"object": "charge", "source": {"object": "card", "fingerprint": "fp_src", "country": "FR"}}
PAYPAL = {"payer": {"payer_info": {"payer_id": "PAYER1", "country_code": "GB"}}}


def test_stripe_shapes():
    assert stripe_card(PAYMENT_INTENT)["fingerprint"] == "fp_ok"
    assert stripe_card(LATEST_CHARGE)["fingerprint"] == "fp_lc"
    assert stripe_card(LEGACY_SOURCE)["fingerprint"] == "fp_src"
    assert stripe_card({"object": "payment_intent"}) == {}
    assert payment_country("stripe", PAYMENT_INTENT) == "ES"
    assert payment_country("paypal", PAYPAL) == "GB"
    assert payment_country("banktransfer", {"iban": "PT50 0000"}) == "PT"
    assert payment_country("stripe", "not a dict") is None


def test_payment_intent_sets_country_but_never_links_buyers(make_edition, series):
    e24, e26 = make_edition(2024), make_edition(2026)
    e24.order("old-mail@example.org", provider="stripe", payment_info=PAYMENT_INTENT, country="")
    o = e26.order("new-mail@example.org", provider="stripe", payment_info=PAYMENT_INTENT, country="")
    resync_series(series)
    fact = AnalyticsOrderFact.objects.get(event=e26.event, order_code=o.code)
    assert fact.country_code == ""                         # card issuer: probable only
    assert (fact.country_inferred, fact.country_inferred_source) == ("ES", "card_issuer")
    assert fact.is_repeat_buyer is False  # same card, different e-mail: not proof of one person


def test_paypal_v2_shapes():
    from pretix_event_analytics.services.payment_info import paypal_countries, paypal_payer

    v2 = {"payer": {"payer_id": "P2", "address": {"country_code": "ES"}},
          "purchase_units": [{"shipping": {"address": {"country_code": "FR"}}}]}
    assert paypal_countries(v2) == {"paypal_address": "FR", "paypal_account": "ES"}
    assert paypal_payer(v2)["payer_id"] == "P2"
    src = {"payer": {"payer_id": "P3"}, "payment_source": {"paypal": {"address": {"country_code": "DE"}}}}
    assert paypal_countries(src) == {"paypal_account": "DE"}
    assert payment_country("paypal2", src) == "DE"
    assert paypal_countries(PAYPAL) == {"paypal_account": "GB"}   # v1 still works
