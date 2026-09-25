"""
Country resolver — determines buyer country for an order.
"""
import logging

import pycountry

logger = logging.getLogger(__name__)

# Keywords to identify a "country of residence" type question
_COUNTRY_KEYWORDS = ("country", "land", "paese", "país", "residence")

def _is_country_question(question_text: str) -> bool:
    lower = question_text.lower()
    return any(kw in lower for kw in _COUNTRY_KEYWORDS)

def resolve_country(order, positions=None, payment=None) -> str:
    """
    Return an ISO 3166-1 alpha-2 country code for the order.

    Resolution priority:
      1. Payment Provider Metadata (Stripe card origin, PayPal country, IBAN prefix)
      2. InvoiceAddress.country (explicitly provided by buyer)
      3. Custom question labelled "Country of residence" or similar
      4. 'UNKNOWN'

    :param order: Pretix Order instance.
    :returns: Two-letter uppercase country code, or 'UNKNOWN'.
    """
    # 1. Payment Provider Metadata (High Confidence)
    confirmed_payment = payment if payment is not None else last_confirmed_payment(order)
    if confirmed_payment and confirmed_payment.info_data:
        info = confirmed_payment.info_data
        provider = confirmed_payment.provider

        if provider == "stripe":
            # Stripe provides the 2-letter country code of the card's issuing bank
            stripe_country = info.get("payment_method_details", {}).get("card", {}).get("country")
            if stripe_country and len(stripe_country) == 2:
                return stripe_country.upper()
                
        elif provider == "paypal":
            # PayPal provides the registered country code of the buyer's account
            paypal_country = info.get("payer", {}).get("payer_info", {}).get("country_code")
            if paypal_country and len(paypal_country) == 2:
                return paypal_country.upper()
                
        elif provider == "banktransfer":
            # Extract first two letters of the IBAN (always the ISO country code)
            iban = info.get("iban", "")
            if iban and len(iban) >= 2:
                iban_country = iban[:2].upper()
                if iban_country.isalpha():
                    return iban_country

    # 2. InvoiceAddress
    try:
        ia = order.invoice_address
        country = str(ia.country) if ia.country else ""
        if country and len(country) == 2:
            return country.upper()
    except AttributeError:
        pass  # No invoice_address relationship
    except Exception:
        logger.debug("analytics: failed to read invoice_address for order %s", order.code, exc_info=True)

    # 3. Custom question containing country-related keywords
    for position in (positions if positions is not None else order.positions.all()):
        for answer in position.answers.all():
            question_text = str(answer.question.question)
            if not _is_country_question(question_text):
                continue
            raw = (answer.answer or "").strip()
            if len(raw) == 2 and raw.isalpha():
                return raw.upper()
            
            # Try to resolve full country names into ISO alpha-2 codes using pycountry
            try:
                # pycountry.countries.search_fuzzy() can handle variations and partial matches
                matches = pycountry.countries.search_fuzzy(raw)
                if matches:
                    return matches[0].alpha_2
            except LookupError:
                pass
            
            continue

    return "UNKNOWN"


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
