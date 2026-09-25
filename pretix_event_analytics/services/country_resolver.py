"""
Country resolver — the buyer's country of residence, and where it came from.

Sources, most to least reliable for *residence* (the first one found wins):

  question        a "Country of residence" question (Pretix type Country, or a
                  text/choice question labelled with "country"/"país"/…)
  invoice         the invoice address the buyer entered
  paypal_address  PayPal shipping address
  paypal_account  PayPal account country
  card_billing    billing address entered with the card (Stripe)
  id_document     issuing country of the ID document (opt-in, see id_country)
  iban            bank-transfer IBAN prefix
  card_issuer     country of the bank that issued the card — Revolut, N26,
                  Wise … issue cards abroad, so this is shown as "by card"

Nothing is guessed beyond that: e-mail domains, names and phone formats are
not used. No source → no country ("unknown").

A separate "travelling from" question is read into its own field.
"""
import logging
import re
from typing import Iterable, Optional, Tuple

import pycountry

logger = logging.getLogger(__name__)

SOURCES = ("question", "invoice", "paypal_address", "paypal_account", "card_billing",
           "id_document", "iban", "card_issuer")
PAYMENT_SOURCES_BEFORE_DOCUMENT = ("paypal_address", "paypal_account", "card_billing")
PAYMENT_SOURCES_AFTER_DOCUMENT = ("iban", "card_issuer")

_TRAVEL_RE = re.compile(r"travel|coming from|arriving from|viaja|vindo de|vens de|vem de|partida|anreise", re.I)
_COUNTRY_RE = re.compile(r"\b(country|land|paese|pa[ií]s|pays|residence|resid[eê]ncia|wohnsitz)\b", re.I)
# Country questions that are not about residence.
_NOT_RESIDENCE_RE = re.compile(r"birth|nascimento|nationalit|nacionalidad|passport|passaporte|issu|emiss", re.I)

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

    found = {}
    confirmed_payment = payment if payment is not None else last_confirmed_payment(order)
    if confirmed_payment and confirmed_payment.info_data:
        from .payment_info import payment_countries

        found = payment_countries(confirmed_payment.provider, confirmed_payment.info_data)
    for source in PAYMENT_SOURCES_BEFORE_DOCUMENT:
        if source in found:
            return found[source], source
    if document_country:
        return document_country, "id_document"
    for source in PAYMENT_SOURCES_AFTER_DOCUMENT:
        if source in found:
            return found[source], source
    return "", ""


def resolve_country(order, positions=None, payment=None) -> str:
    """Two-letter country code, or 'UNKNOWN' (2.0 API)."""
    code, _source = resolve_country_source(order, positions, payment)
    return code or "UNKNOWN"


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
