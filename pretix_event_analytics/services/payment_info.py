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


def _code(value) -> Optional[str]:
    if isinstance(value, str) and len(value) == 2 and value.isalpha():
        return value.upper()
    return None


def stripe_billing_country(info: Dict) -> Optional[str]:
    """Country of the card's billing address, when the buyer entered one."""
    if not isinstance(info, dict):
        return None
    for obj in _charges(info):
        address = ((obj.get("billing_details") or {}).get("address")) or {}
        code = _code(address.get("country"))
        if code:
            return code
    return None


def paypal_countries(info: Dict) -> Dict[str, str]:
    """{"paypal_address": …, "paypal_account": …} from PayPal v1 or v2 order data."""
    if not isinstance(info, dict):
        return {}
    out = {}
    payer_info = paypal_payer(info)
    shipping = (payer_info.get("shipping_address") or {}).get("country_code")
    for unit in info.get("purchase_units") or []:
        if isinstance(unit, dict) and not shipping:
            shipping = (((unit.get("shipping") or {}).get("address")) or {}).get("country_code")
    account = payer_info.get("country_code") or ((info.get("payer") or {}).get("address") or {}).get("country_code")
    if _code(shipping):
        out["paypal_address"] = _code(shipping)
    if _code(account):
        out["paypal_account"] = _code(account)
    return out


def payment_countries(provider: str, info: Dict) -> Dict[str, str]:
    """
    Every country the payment reveals, keyed by source:
    paypal_address, paypal_account, card_billing, iban, card_issuer.
    """
    out: Dict[str, str] = {}
    if provider in STRIPE_PROVIDERS:
        billing = stripe_billing_country(info)
        if billing:
            out["card_billing"] = billing
        issuer = _code(stripe_card(info).get("country"))
        if issuer:
            out["card_issuer"] = issuer
    elif provider in PAYPAL_PROVIDERS:
        out.update(paypal_countries(info))
    elif provider == "banktransfer":
        iban = str((info or {}).get("iban") or "").replace(" ", "")
        if _code(iban[:2]):
            out["iban"] = iban[:2].upper()
    return out


def payment_country(provider: str, info: Dict) -> Optional[str]:
    """Best single country from payment details, or None (kept for callers of 2.0)."""
    found = payment_countries(provider, info)
    for source in ("paypal_address", "paypal_account", "card_billing", "iban", "card_issuer"):
        if source in found:
            return found[source]
    return None
