"""Country of residence: sources in order, nothing guessed; travel country; ID-document country (opt-in)."""
import pytest
from django_scopes import scopes_disabled

from pretix_event_analytics.models import AnalyticsOrderFact, AnalyticsTicketFact
from pretix_event_analytics.services.country_resolver import parse_country
from pretix_event_analytics.services.id_country import document_country
from pretix_event_analytics.services.resync_service import resync_event

CARD_ES = {"charges": {"data": [{"payment_method_details": {"card": {"fingerprint": "fp", "country": "ES"}}}]}}
CARD_ES_BILLING_FR = {"charges": {"data": [{
    "payment_method_details": {"card": {"fingerprint": "fp", "country": "ES"}},
    "billing_details": {"address": {"country": "FR"}},
}]}}


def _fact(order):
    resync_event(order.event)
    return AnalyticsOrderFact.objects.get(event=order.event, order_code=order.code)


def _question(kit, label, qtype="S"):
    with scopes_disabled():
        return kit.event.questions.create(question=label, type=qtype, required=False)


@pytest.mark.parametrize("raw,code", [
    ("PT", "PT"), ("pt", "PT"), ("Portugal", "PT"), ("España", "ES"), ("germany", "DE"),
    ("United Kingdom", "GB"), ("Lisbon", None), ("XX", None), ("", None),
])
def test_parse_country_is_exact(raw, code):
    assert parse_country(raw) == code


def test_invoice_address_beats_card_issuer(make_edition):
    kit = make_edition(2026)
    f = _fact(kit.order("a@example.org", country="DE", provider="stripe", payment_info=CARD_ES))
    assert (f.country_code, f.country_source) == ("DE", "invoice")


def test_card_billing_beats_card_issuer(make_edition):
    kit = make_edition(2026)
    f = _fact(kit.order("a@example.org", country="", provider="stripe", payment_info=CARD_ES_BILLING_FR))
    assert (f.country_code, f.country_source) == ("FR", "card_billing")


def test_card_issuer_is_only_probable(make_edition):
    kit = make_edition(2026)
    f = _fact(kit.order("a@example.org", country="", provider="stripe", payment_info=CARD_ES))
    assert (f.country_code, f.country_source) == ("", "")                    # a bank is not a residence
    assert (f.bank_country, f.bank_country_source) == ("ES", "card_issuer")
    assert (f.country_inferred, f.country_inferred_source) == ("ES", "card_issuer")


def test_iban_is_only_probable(make_edition):
    kit = make_edition(2026)
    f = _fact(kit.order("a@example.org", country="", provider="banktransfer",
                        payment_info={"iban": "DE89370400440532013000"}))
    assert f.country_code == ""
    assert (f.country_inferred, f.country_inferred_source) == ("DE", "iban")


def test_no_source_means_unknown(make_edition):
    kit = make_edition(2026)
    f = _fact(kit.order("someone@example.de", country=""))
    assert (f.country_code, f.country_source) == ("", "")  # e-mail domains are never used


def test_residence_question_beats_invoice_and_travel_is_separate(make_edition):
    kit = make_edition(2026)
    residence = _question(kit, "Country of residence", "CC")
    travel = _question(kit, "Which country are you travelling from?", "CC")
    o = kit.order("a@example.org", [{"item": kit.ga, "answers": {residence: "NL", travel: "ES"}}], country="PT")
    f = _fact(o)
    assert (f.country_code, f.country_source, f.travel_country_code) == ("NL", "question", "ES")


def test_country_of_birth_question_is_not_residence(make_edition):
    kit = make_edition(2026)
    q = _question(kit, "Country of birth", "CC")
    f = _fact(kit.order("a@example.org", [{"item": kit.ga, "answers": {q: "BR"}}], country=""))
    assert f.country_code == ""


def _id_question(kit):
    q = _question(kit, "ID Number")
    cfg = kit.event.analytics_config
    cfg.id_question_id = q.pk
    cfg.save()
    return q


def test_id_document_only_when_opted_in(make_edition):
    kit = make_edition(2026)
    q = _question(kit, "ID Number")
    o = kit.order("a@example.org", [{"item": kit.ga, "answers": {q: "X1234567L"}}], country="")
    f = _fact(o)
    assert f.country_code == ""
    assert AnalyticsTicketFact.objects.get(order_fact=f).document_country == ""
    cfg = kit.event.analytics_config
    cfg.id_question_id = q.pk
    cfg.save()
    assert _fact(o).country_code == "ES"


def test_residence_document_is_exact_residence_not_nationality(make_edition):
    kit = make_edition(2026)
    q = _id_question(kit)
    f = _fact(kit.order("a@example.org", [{"item": kit.ga, "answers": {q: "X1234567L"}}], country=""))  # NIE
    assert (f.country_code, f.country_source) == ("ES", "id_document")
    t = AnalyticsTicketFact.objects.get(order_fact=f)
    assert (t.document_country, t.nationality) == ("ES", "")                  # NIE holders are foreigners


def test_citizen_document_is_nationality_and_only_probable_residence(make_edition):
    kit = make_edition(2026)
    q = _id_question(kit)
    f = _fact(kit.order("a@example.org", [{"item": kit.ga, "answers": {q: "12345678-Z"}}], country=""))  # DNI
    assert (f.country_code, f.country_source) == ("", "")
    assert f.nationality == "ES"
    assert (f.country_inferred, f.country_inferred_source) == ("ES", "nationality")
    t = AnalyticsTicketFact.objects.get(order_fact=f)
    assert (t.nationality, t.nationality_source) == ("ES", "id_document")


def test_nationality_question_is_not_residence(make_edition):
    kit = make_edition(2026)
    nat = _question(kit, "Nationality")
    f = _fact(kit.order("a@example.org", [{"item": kit.ga, "answers": {nat: "Portuguese"}}], country="DE"))
    assert (f.country_code, f.country_source) == ("DE", "invoice")            # residence stays the invoice
    t = AnalyticsTicketFact.objects.get(order_fact=f)
    assert (t.nationality, t.nationality_source) == ("PT", "question")


def test_nationality_is_per_ticket_holder(make_edition):
    kit = make_edition(2026)
    q = _id_question(kit)
    o = kit.order("a@example.org", [
        {"item": kit.ga, "attendee_email": "a@example.org", "answers": {q: "12345678Z"}},  # buyer: ES DNI
        {"item": kit.ga, "attendee_email": "b@example.org", "answers": {q: "87654321"}},   # friend: PT civil no.
    ], country="")
    f = _fact(o)
    assert f.nationality == "ES"                                              # the buyer's own ticket
    assert sorted(AnalyticsTicketFact.objects.filter(order_fact=f).values_list("nationality", flat=True)) == ["ES", "PT"]


def test_id_document_ranks_below_stated_sources(make_edition):
    kit = make_edition(2026)
    q = _id_question(kit)
    f = _fact(kit.order("a@example.org", [{"item": kit.ga, "answers": {q: "X1234567L"}}], country="PT"))
    assert (f.country_code, f.country_source) == ("PT", "invoice")


def test_group_with_mixed_documents_says_nothing(make_edition):
    kit = make_edition(2026)
    q = _question(kit, "ID Number")
    cfg = kit.event.analytics_config
    cfg.id_question_id = q.pk
    cfg.save()
    o = kit.order("a@example.org", [
        {"item": kit.ga, "answers": {q: "12345678Z"}},        # ES DNI
        {"item": kit.ga, "answers": {q: "123456789012"}},     # not a known format
        {"item": kit.ga, "answers": {q: "X1234567L"}},        # ES NIE
    ], country="")
    assert _fact(o).country_code == "ES"                      # the only residence document (NIE) decides
    o2 = kit.order("b@example.org", [
        {"item": kit.ga, "answers": {q: "12345678Z"}},
        {"item": kit.ga, "answers": {q: "87654321"}},         # PT civil number
    ], country="")
    assert _fact(o2).country_code == ""


@pytest.mark.parametrize("raw,code", [
    ("12345678Z", "ES"), ("12345678A", None),      # DNI check letter
    ("X1234567L", "ES"),                          # NIE
    ("87654321", "PT"),                           # PT civil number (8 digits)
    ("AB123456", None), ("", None), ("12", None),
    ("00000000", None), ("11111111", None),        # placeholders
])
def test_document_country_rules(raw, code):
    assert document_country(raw) == code


@pytest.mark.parametrize("raw,info", [
    ("12345678Z", ("ES", "nationality")), ("X1234567L", ("ES", "residence")),
    ("87654321", ("PT", "nationality")),
    ("RSSMRA85T10A562S", ("IT", "nationality")), ("RSSMRA85T10Z404X", ("", "")),   # born in Italy / abroad
])
def test_document_info_says_what_it_proves(raw, info):
    from pretix_event_analytics.services.id_country import document_info
    assert document_info(raw) == info


def test_other_orders_beat_nationality_beat_bank_beat_email(make_edition, series):
    from pretix_event_analytics.services.resync_service import resync_series

    e24, e26 = make_edition(2024), make_edition(2026)
    q = _id_question(e26)
    e24.order("fan@sapo.pt", country="NL")
    both = e26.order("fan@sapo.pt", [{"item": e26.ga, "answers": {q: "12345678Z"}}], country="",
                     provider="stripe", payment_info=CARD_ES)
    nat = e26.order("x@sapo.pt", [{"item": e26.ga, "answers": {q: "12345678Z"}}], country="",
                    provider="stripe", payment_info={**CARD_ES})
    bank = e26.order("y@sapo.pt", country="", provider="banktransfer", payment_info={"iban": "DE89370400440532013000"})
    resync_series(series)
    got = {o.code: AnalyticsOrderFact.objects.get(order_code=o.code) for o in (both, nat, bank)}
    assert (got[both.code].country_inferred, got[both.code].country_inferred_source) == ("NL", "other_order")
    assert (got[nat.code].country_inferred, got[nat.code].country_inferred_source) == ("ES", "nationality")
    assert (got[bank.code].country_inferred, got[bank.code].country_inferred_source) == ("DE", "iban")


# ── Derived countries: inferred (other orders) and probable (e-mail domain) ──

from pretix_event_analytics.services.country_resolver import email_domain_country  # noqa: E402


@pytest.mark.parametrize("email,code", [
    ("a@sapo.pt", "PT"), ("a@x.co.uk", "GB"), ("a@web.de", "DE"), ("a@gmail.com", ""), ("a@startup.io", ""),
    ("a@x.eu", ""), ("broken", ""),
])
def test_email_domain_country(email, code):
    assert email_domain_country(email) == code


def test_same_customer_other_order_gives_inferred_country(make_edition, series):
    from pretix_event_analytics.services.resync_service import resync_series

    e24, e26 = make_edition(2024), make_edition(2026)
    e24.order("fan@gmail.com", country="ES")
    free = e26.order("fan@gmail.com", country="")                # e.g. a child ticket, no payment data
    resync_series(series)
    f = AnalyticsOrderFact.objects.get(order_code=free.code)
    assert f.country_code == ""                                  # never mixed into the exact country
    assert (f.country_inferred, f.country_inferred_source) == ("ES", "other_order")


def test_conflicting_other_orders_fall_back_to_probable_or_nothing(make_edition, series):
    from pretix_event_analytics.services.resync_service import resync_series

    e23, e24, e26 = make_edition(2023), make_edition(2024), make_edition(2026)
    e23.order("mover@mail.de", country="ES")
    e24.order("mover@mail.de", country="FR")
    o = e26.order("mover@mail.de", country="")
    nobody = e26.order("nobody@gmail.com", country="")
    resync_series(series)
    f = AnalyticsOrderFact.objects.get(order_code=o.code)
    assert (f.country_inferred, f.country_inferred_source) == ("DE", "email_domain")
    n = AnalyticsOrderFact.objects.get(order_code=nobody.code)
    assert (n.country_inferred, n.country_inferred_source) == ("", "")


def test_exact_country_clears_derived_fields(make_edition):
    kit = make_edition(2026)
    f = _fact(kit.order("a@sapo.pt", country="NL"))
    assert (f.country_code, f.country_inferred, f.email_country) == ("NL", "", "PT")


def test_audience_country_switch(admin_client, make_edition, series):
    from django.urls import reverse
    from pretix_event_analytics.services.resync_service import resync_series

    e24, e26 = make_edition(2024), make_edition(2026)
    e24.order("fan@gmail.com", country="ES")
    e26.order("fan@gmail.com", country="")
    e26.order("x@sapo.pt", country="")
    e26.order("y@example.org", country="NL")
    resync_series(series)
    url = reverse("plugins:pretix_event_analytics:audience",
                  kwargs={"organizer": e26.event.organizer.slug, "event": e26.event.slug})

    def countries(mode):
        data = admin_client.get(url, {"countries": mode}).context["data"]
        return {c["code"]: c["orders"] for c in data["countries"]}, {r["source"]: r["orders"] for r in data["country_sources"]}

    exact, sources = countries("exact")
    assert exact == {"NL": 1, "": 2}
    assert sources == {"invoice": 1, "other_order": 1, "email_domain": 1}
    assert countries("inferred")[0] == {"NL": 1, "ES": 1, "": 1}
    assert countries("probable")[0] == {"NL": 1, "ES": 1, "PT": 1}


def test_audience_shows_nationality_apart_from_residence(admin_client, make_edition):
    from django.urls import reverse

    kit = make_edition(2026)
    q = _id_question(kit)
    kit.order("a@example.org", [{"item": kit.ga, "answers": {q: "12345678Z"}}], country="PT")   # ES citizen in PT
    resync_event(kit.event)
    url = reverse("plugins:pretix_event_analytics:audience",
                  kwargs={"organizer": kit.event.organizer.slug, "event": kit.event.slug})
    resp = admin_client.get(url)
    data = resp.context["data"]
    assert {c["code"]: c["orders"] for c in data["countries"]} == {"PT": 1}        # residence
    assert [(n["code"], n["n"]) for n in data["nationalities"]] == [("ES", 1)]      # nationality
    assert b"Nationality" in resp.content
