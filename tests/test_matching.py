"""
Person matching (2.1): name + birth date links people; e-mails and payment
methods are only supporting evidence; when in doubt a ticket stays unknown.
"""
import datetime

from pretix_event_analytics.models import AnalyticsTicketFact
from pretix_event_analytics.services.identity_keys import name_tokens, person_keys
from pretix_event_analytics.services.resync_service import resync_series


def _ticket(order):
    return AnalyticsTicketFact.objects.get(order_fact__order_code=order.code, event=order.event, is_addon=False)


def _pair(make_edition, series, old, new, *, old_email="a@example.org", new_email="b@example.org",
          old_kw=None, new_kw=None):
    e24, e26 = make_edition(2024), make_edition(2026)
    o24 = e24.order(old_email, [{"item": e24.ga, **old}], **(old_kw or {}))
    o26 = e26.order(new_email, [{"item": e26.ga, **new}], **(new_kw or {}))
    resync_series(series)
    return _ticket(o24), _ticket(o26)


# ── Normalisation ─────────────────────────────────────────────────────────────

def test_name_tokens_normalise_accents_case_particles_and_initials():
    assert name_tokens("  João M. da SILVA-Santos ") == ["joao", "silva", "santos"]
    assert name_tokens("Ana") == ["ana"]


def test_single_word_name_gives_no_key():
    assert person_keys("Ana", datetime.date(1990, 1, 1)) == []


def test_implausible_birth_date_gives_no_certain_key():
    types = {k["type"] for k in person_keys("Ana Silva", datetime.date(2025, 1, 1), datetime.date(2026, 8, 1))}
    assert types == {"nm", "fl"}


# ── Certain tier ──────────────────────────────────────────────────────────────

def test_same_name_different_accents_and_order_is_same_person(make_edition, series):
    _old, new = _pair(make_edition, series, {"attendee_name": "João Silva", "birth": "1990-04-01"},
                      {"attendee_name": "silva joao", "birth": "1990-04-01"})
    assert new.is_returning_attendee and new.attendee_match == "certain"


def test_middle_name_added_is_same_person(make_edition, series):
    _old, new = _pair(make_edition, series, {"attendee_name": "Maria Costa", "birth": "1985-12-24"},
                      {"attendee_name": "Maria Inês Costa", "birth": "1985-12-24"})
    assert new.is_returning_attendee


def test_second_surname_added_is_same_person(make_edition, series):
    _old, new = _pair(make_edition, series, {"attendee_name": "Rui Alves", "birth": "1993-06-15"},
                      {"attendee_name": "Rui Alves Pereira", "birth": "1993-06-15"})
    assert new.is_returning_attendee


def test_same_name_different_birth_date_is_different_person(make_edition, series):
    _old, new = _pair(make_edition, series, {"attendee_name": "Ana Silva", "birth": "1990-01-01"},
                      {"attendee_name": "Ana Silva", "birth": "1994-09-09"},
                      old_email="same@example.org", new_email="same@example.org")
    assert not new.is_returning_attendee and not new.is_returning_attendee_incl


# ── Payment / e-mail never link alone ─────────────────────────────────────────

def test_shared_card_different_names_is_not_same_person(make_edition, series):
    card = {"payment_method_details": {"card": {"fingerprint": "fp_family", "country": "PT"}}}
    kw = {"provider": "stripe", "payment_info": card}
    _old, new = _pair(make_edition, series, {"attendee_name": "Teresa Mota", "birth": "1970-03-03"},
                      {"attendee_name": "Tiago Mota", "birth": "2001-11-11"}, old_kw=kw, new_kw=kw)
    assert not new.is_returning_attendee and not new.is_returning_attendee_incl


def test_same_attendee_email_different_names_is_not_same_person(make_edition, series):
    _old, new = _pair(make_edition, series,
                      {"attendee_name": "Luis Braga", "birth": "1980-01-01", "attendee_email": "fam@example.org"},
                      {"attendee_name": "Lara Braga", "birth": "2000-01-01", "attendee_email": "fam@example.org"})
    assert not new.is_returning_attendee and not new.is_returning_attendee_incl


# ── Probable tier and "when in doubt, unknown" ────────────────────────────────

def test_missing_birth_date_with_shared_email_is_probable(make_edition, series):
    old, new = _pair(make_edition, series, {"attendee_name": "Nuno Faria"},
                     {"attendee_name": "Nuno Faria", "birth": "1989-05-05"},
                     old_email="nuno@example.org", new_email="nuno@example.org")
    assert old.attendee_match == "probable" and old.attendee_person_key == ""
    assert new.attendee_match == "certain"
    assert new.is_returning_attendee is False          # certain view: not proven
    assert new.is_returning_attendee_incl is True      # incl. probable matches


def test_missing_birth_date_without_evidence_stays_unknown(make_edition, series):
    old, new = _pair(make_edition, series, {"attendee_name": "Nuno Faria"},
                     {"attendee_name": "Nuno Faria", "birth": "1989-05-05"})
    assert old.attendee_match == "" and old.attendee_person_key_incl == ""
    assert not new.is_returning_attendee_incl


def test_missing_birth_date_with_two_candidates_stays_unknown(make_edition, series):
    e24, e26 = make_edition(2024), make_edition(2026)
    old = e24.order("home@example.org", [{"item": e24.ga, "attendee_name": "Rita Lima"}])
    e26.order("home@example.org", [
        {"item": e26.ga, "attendee_name": "Rita Lima", "birth": "1970-02-02"},   # mother
        {"item": e26.ga, "attendee_name": "Rita Lima", "birth": "2003-02-02"},   # daughter
    ])
    resync_series(series)
    assert _ticket(old).attendee_match == ""


def test_placeholder_identity_on_many_tickets_stays_unknown(make_edition, series):
    e26 = make_edition(2026)
    o = e26.order("group@example.org", [
        {"item": e26.ga, "attendee_name": "Test Person", "birth": "1990-01-01"} for _ in range(3)
    ])
    resync_series(series)
    tickets = AnalyticsTicketFact.objects.filter(order_fact__order_code=o.code)
    assert {t.attendee_match for t in tickets} == {""}



# ── Legacy lists with name + birth date ───────────────────────────────────────

def test_legacy_csv_with_names_marks_ticket_holders_returning(make_edition, series):
    from pretix_event_analytics.services.legacy import _count, import_legacy_list
    from pretix_event_analytics.models import LegacyEdition

    e26 = make_edition(2026)
    back = e26.order("x@example.org", [{"item": e26.ga, "attendee_name": "Inês Carvalho", "birth": "1994-10-03"}])
    new = e26.order("y@example.org", [{"item": e26.ga, "attendee_name": "Hugo Neves", "birth": "1994-10-03"}])
    csv_text = "First name;Last name;Date of birth;E-mail\nInes;Carvalho;03/10/1994;ines@example.org\nNo;Birthdate;;\n"
    result = import_legacy_list(series, "Suti 2019", 2019, csv_text)
    assert result["people"] == 2 and result["imported"] == 2
    assert _count(LegacyEdition.objects.get()) == 2
    resync_series(series)
    assert _ticket(back).is_returning_attendee and _ticket(back).attendee_first_seen_year == 2019
    assert not _ticket(new).is_returning_attendee


def test_legacy_plain_email_list_counts_customers_only(make_edition, series):
    from pretix_event_analytics.models import AnalyticsOrderFact
    from pretix_event_analytics.services.legacy import import_legacy_list

    e26 = make_edition(2026)
    o = e26.order("old@example.org", [{"item": e26.ga, "attendee_name": "Olga Rios", "birth": "1980-01-01"}])
    import_legacy_list(series, "Suti 2019", 2019, "old@example.org, other@example.org")
    resync_series(series)
    assert AnalyticsOrderFact.objects.get(order_code=o.code).is_repeat_buyer   # returning customer
    assert not _ticket(o).is_returning_attendee                                # but not a proven person
