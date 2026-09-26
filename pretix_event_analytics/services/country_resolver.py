"""
Country resolver — the buyer's country of residence, and where it came from.

Sources, most to least reliable for *residence* (the first one found wins):

  question        a "Country of residence" question (Pretix type Country, or a
                  text/choice question labelled with "country"/"país"/…)
  invoice         the invoice address the buyer entered
  paypal_address  PayPal shipping address
  paypal_account  PayPal account country
  card_billing    billing address entered with the card (Stripe)
  id_document     an ID document that proves *residence* (Spanish NIE, UK driving
                  licence, Belgian national number — see id_country.PROVES)

Bank countries are NOT residence and are kept out of the exact country. They are
returned by ``resolve_bank_country`` and shown as *probable* (like e-mail domains):

  card_issuer     country of the bank that issued the card — Revolut, N26,
                  Wise … issue cards abroad; measured on SUTI 2026 it agreed with
                  the ID document 79% and the invoice address 86% of the time
  iban            bank-transfer IBAN prefix (same weakness)

Nothing is guessed beyond that: e-mail domains, names and phone formats are
not used. No source → no country ("unknown").

A separate "travelling from" question is read into its own field. Nationality is
kept apart too (``resolve_nationality``, per ticket holder): a nationality question,
else an ID document that proves citizenship. It is a *probable* residence hint only.
"""
import logging
import re
from typing import Iterable, Optional, Tuple

import pycountry

logger = logging.getLogger(__name__)

SOURCES = ("question", "invoice", "paypal_address", "paypal_account", "card_billing", "id_document")
PAYMENT_SOURCES_BEFORE_DOCUMENT = ("paypal_address", "paypal_account", "card_billing")
# Bank countries: probable only, in this order (never written to country_code).
BANK_SOURCES = ("card_issuer", "iban")

_TRAVEL_RE = re.compile(r"travel|coming from|arriving from|viaja|vindo de|vens de|vem de|partida|anreise", re.I)
_COUNTRY_RE = re.compile(r"\b(country|land|paese|pa[ií]s|pays|residence|resid[eê]ncia|wohnsitz)\b", re.I)
_NATIONALITY_RE = re.compile(r"nationalit|nacionalidad|staatsangeh|citizenship|cidadania|ciudadan", re.I)
# Country questions that are not about residence.
_NOT_RESIDENCE_RE = re.compile(r"birth|nascimento|nationalit|nacionalidad|staatsangeh|citizenship|cidadania|ciudadan|"
                               r"passport|passaporte|issu|emiss", re.I)
# Answers to a nationality question are often demonyms rather than country names.
_DEMONYMS = {
    "portuguese": "PT", "portugues": "PT", "portuguesa": "PT", "spanish": "ES", "espanol": "ES", "espanola": "ES",
    "espanhol": "ES", "espanhola": "ES", "british": "GB", "english": "GB", "scottish": "GB", "welsh": "GB",
    "irish": "IE", "french": "FR", "frances": "FR", "francesa": "FR", "german": "DE", "alemao": "DE", "alema": "DE",
    "deutsch": "DE", "italian": "IT", "italiano": "IT", "italiana": "IT", "dutch": "NL", "holandes": "NL",
    "holandesa": "NL", "belgian": "BE", "swiss": "CH", "suico": "CH", "suica": "CH", "austrian": "AT",
    "brazilian": "BR", "brasileiro": "BR", "brasileira": "BR", "american": "US", "israeli": "IL", "polish": "PL",
    "swedish": "SE", "danish": "DK", "norwegian": "NO", "finnish": "FI", "czech": "CZ", "greek": "GR",
    "australian": "AU", "canadian": "CA", "ukrainian": "UA", "russian": "RU", "romanian": "RO",
}

_ALIASES = {
    "uk": "GB", "england": "GB", "scotland": "GB", "wales": "GB", "great britain": "GB", "inglaterra": "GB",
    "reino unido": "GB", "usa": "US", "eua": "US", "estados unidos": "US", "espana": "ES", "espanha": "ES",
    "alemanha": "DE", "deutschland": "DE", "franca": "FR", "holanda": "NL", "nederland": "NL",
    "the netherlands": "NL", "italia": "IT", "suica": "CH", "schweiz": "CH", "suisse": "CH", "belgica": "BE",
    "belgie": "BE", "brasil": "BR", "irlanda": "IE", "polska": "PL", "grecia": "GR", "suecia": "SE",
    "dinamarca": "DK", "noruega": "NO", "austria": "AT", "osterreich": "AT", "republica checa": "CZ",
    "czechia": "CZ", "russia": "RU", "turkey": "TR", "turquia": "TR",
}


def _is_country_question(question_text: str) -> bool:
    return bool(_COUNTRY_RE.search(question_text)) and not _TRAVEL_RE.search(question_text) \
        and not _NOT_RESIDENCE_RE.search(question_text)


def _is_nationality_question(question_text: str) -> bool:
    return bool(_NATIONALITY_RE.search(question_text))


def _is_travel_question(question_text: str) -> bool:
    return bool(_TRAVEL_RE.search(question_text))


def parse_country(raw: str) -> Optional[str]:
    """ISO code for an exact country code or name; None when unsure (no fuzzy matching)."""
    import unicodedata

    raw = (raw or "").strip()
    if not raw:
        return None
    if len(raw) == 2 and raw.isalpha():
        code = raw.upper()
        return code if pycountry.countries.get(alpha_2=code) or code == "XK" else None
    plain = "".join(c for c in unicodedata.normalize("NFKD", raw) if not unicodedata.combining(c)).lower().strip(" .")
    if plain in _ALIASES:
        return _ALIASES[plain]
    try:
        return pycountry.countries.lookup(raw).alpha_2
    except LookupError:
        return None


def _answer_country(positions: Iterable, match) -> Optional[str]:
    for position in positions:
        for answer in position.answers.all():
            if match(str(answer.question.question)):
                code = parse_country(answer.answer or "")
                if code:
                    return code
    return None


def resolve_country_source(order, positions=None, payment=None,
                           document_country: str = "") -> Tuple[str, str]:
    """(country code, source) — ("", "") when no source says anything."""
    positions = list(positions if positions is not None else order.positions.all())

    code = _answer_country(positions, _is_country_question)
    if code:
        return code, "question"

    try:
        ia = order.invoice_address
        country = str(ia.country) if ia.country else ""
        if len(country) == 2:
            return country.upper(), "invoice"
    except AttributeError:
        pass  # No invoice_address relationship
    except Exception:
        logger.debug("analytics: failed to read invoice_address for order %s", order.code, exc_info=True)

    found = _payment_countries(order, payment)
    for source in PAYMENT_SOURCES_BEFORE_DOCUMENT:
        if source in found:
            return found[source], source
    if document_country:
        return document_country, "id_document"
    return "", ""


def _payment_countries(order, payment=None) -> dict:
    confirmed_payment = payment if payment is not None else last_confirmed_payment(order)
    if confirmed_payment and confirmed_payment.info_data:
        from .payment_info import payment_countries

        return payment_countries(confirmed_payment.provider, confirmed_payment.info_data)
    return {}


def derived_country(exact: str, other_order: str, nationality: str, bank: str, bank_source: str,
                    email: str) -> dict:
    """The derived residence-country fields, never mixed into the exact country.

    Precedence: exact (nothing derived) > the same customer's other orders (inferred)
    > the buyer's nationality > paying bank > e-mail country domain (all probable).
    """
    if exact:
        return {"country_inferred": "", "country_inferred_source": ""}
    if other_order:
        return {"country_inferred": other_order, "country_inferred_source": "other_order"}
    if nationality:
        return {"country_inferred": nationality, "country_inferred_source": "nationality"}
    if bank:
        return {"country_inferred": bank, "country_inferred_source": bank_source or "card_issuer"}
    if email:
        return {"country_inferred": email, "country_inferred_source": "email_domain"}
    return {"country_inferred": "", "country_inferred_source": ""}


def resolve_bank_country(order, payment=None) -> Tuple[str, str]:
    """(country, source) of the paying bank (card issuer, IBAN) — probable, not residence."""
    found = _payment_countries(order, payment)
    for source in BANK_SOURCES:
        if source in found:
            return found[source], source
    return "", ""


def resolve_country(order, positions=None, payment=None) -> str:
    """Two-letter country code, or 'UNKNOWN' (2.0 API)."""
    code, _source = resolve_country_source(order, positions, payment)
    return code or "UNKNOWN"


# Country domains used as generic/vanity endings, not as a place (never mapped).
_VANITY_TLDS = frozenset({
    "io", "co", "me", "tv", "ai", "ly", "fm", "gg", "to", "cc", "ws", "nu", "tk", "ml", "ga", "cf", "gq", "la",
    "ag", "am", "ac", "sh", "vc", "gd",
})


def email_domain_country(email: str) -> str:
    """
    Country of the e-mail's domain (".pt" → PT, ".uk" → GB), or "".
    A *probable* signal only: measured on SUTI data .pt/.es/.uk ≈ 100%,
    .de/.fr ≈ 60–70%. Generic domains (.com, .org, .eu, .io …) say nothing.
    """
    domain = (email or "").strip().lower().rsplit("@", 1)[-1]
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
    if len(tld) != 2 or not tld.isalpha():
        return ""
    if tld == "uk":
        return "GB"
    if tld in _VANITY_TLDS:
        return ""
    code = tld.upper()
    return code if pycountry.countries.get(alpha_2=code) else ""


def parse_nationality(raw: str) -> Optional[str]:
    """ISO code for a nationality answer: a country code/name or a common demonym ("Portuguese")."""
    import unicodedata

    code = parse_country(raw)
    if code:
        return code
    plain = "".join(c for c in unicodedata.normalize("NFKD", raw or "") if not unicodedata.combining(c))
    return _DEMONYMS.get(plain.lower().strip(" ."))


def resolve_nationality(position, document_country: str = "", document_proves: str = "") -> Tuple[str, str]:
    """(nationality, source) of one ticket holder: a nationality question on the ticket, else an ID
    document that proves citizenship. ("", "") when neither says anything."""
    for answer in position.answers.all():
        if _is_nationality_question(str(answer.question.question)):
            code = parse_nationality(answer.answer or "")
            if code:
                return code, "question"
    if document_country and document_proves == "nationality":
        return document_country, "id_document"
    return "", ""


def resolve_travel_country(positions: Iterable) -> str:
    """Answer to a "travelling from" question, or ""."""
    return _answer_country(positions, _is_travel_question) or ""


def last_confirmed_payment(order):
    """Latest confirmed payment, using prefetched ``payments`` when present."""
    confirmed = [p for p in order.payments.all() if p.state == "confirmed"]
    if not confirmed:
        return None
    return max(confirmed, key=lambda p: (p.payment_date is not None, p.payment_date or p.created, p.pk))


def resolve_city_and_postal(order) -> tuple:
    """
    Extract city and postal code from InvoiceAddress.

    :returns: Tuple of (city: str, postal_code: str).
    """
    try:
        ia = order.invoice_address
        return (ia.city or "").strip(), (ia.zipcode or "").strip()
    except AttributeError:
        return "", ""  # No invoice_address relationship
    except Exception:
        logger.debug("analytics: failed to read city/postal for order %s", order.code, exc_info=True)
        return "", ""
