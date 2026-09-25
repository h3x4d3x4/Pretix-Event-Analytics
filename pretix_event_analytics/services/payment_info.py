"""
Read card details from the payment info Pretix stores for each provider.

Stripe's shape depends on the API object Pretix saved:
  * PaymentIntent (current): ``charges.data[-1].payment_method_details.card``
    or, on newer API versions, an expanded ``latest_charge`` object;
  * Charge (older installs): ``payment_method_details.card`` or ``source.card``
    / ``source`` for legacy card sources.
The first path that yields data wins.
"""
from typing import Dict, Optional

STRIPE_PROVIDERS = ("stripe", "stripe_cc")
PAYPAL_PROVIDERS = ("paypal", "paypal2")


def _charges(info: Dict):
    charges = info.get("charges")
    if isinstance(charges, dict):
        data = charges.get("data") or []
        # The last charge is the one that succeeded after any retries.
        for ch in reversed(data):
            if isinstance(ch, dict):
                yield ch
    latest = info.get("latest_charge")
    if isinstance(latest, dict):
        yield latest
    yield info


def stripe_card(info: Dict) -> Dict:
    """The card dict (fingerprint, country, brand …) or {}."""
    if not isinstance(info, dict):
        return {}
    for obj in _charges(info):
        card = (obj.get("payment_method_details") or {}).get("card")
        if isinstance(card, dict) and (card.get("fingerprint") or card.get("country")):
            return card
        source = obj.get("source")
        if isinstance(source, dict):
            card = source.get("card") if isinstance(source.get("card"), dict) else source
            if card.get("fingerprint") or card.get("country"):
                return card
    return {}


def paypal_payer(info: Dict) -> Dict:
    if not isinstance(info, dict):
        return {}
    return (info.get("payer") or {}).get("payer_info") or {}


def payment_country(provider: str, info: Dict) -> Optional[str]:
    """Two-letter country from payment details, or None."""
    code = None
    if provider in STRIPE_PROVIDERS:
        code = stripe_card(info).get("country")
    elif provider in PAYPAL_PROVIDERS:
        code = paypal_payer(info).get("country_code")
    elif provider == "banktransfer":
        iban = str((info or {}).get("iban") or "")
        code = iban[:2] if iban[:2].isalpha() else None
    if isinstance(code, str) and len(code) == 2 and code.isalpha():
        return code.upper()
    return None
