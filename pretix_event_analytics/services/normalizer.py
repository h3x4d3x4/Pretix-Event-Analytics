"""
Order normalizer — the main orchestration service.

Transforms a raw Pretix Order into the data needed to populate
AnalyticsOrderFact and AnalyticsTicketFact.  Calls all sub-services
and returns two dicts ready for model creation.

This module deliberately has no Django model imports — it only returns
plain dicts so that callers (tasks.py, management command) control
when DB writes happen.
"""
from datetime import timedelta
from decimal import Decimal
from typing import Dict, List, Tuple

from .age_bucketer import resolve_age_confirmed, resolve_age_range
from .country_resolver import resolve_city_and_postal, resolve_country
from .hash_service import generate_repeat_hash
from .van_length_bucketer import resolve_caravan_data, resolve_caravan_data_for_position


def normalize_order(order, config) -> Tuple[Dict, List[Dict]]:
    """
    Normalize a Pretix Order into order-level and ticket-level fact dicts.

    :param order: Pretix Order instance with related data prefetched.
    :param config: EventAnalyticsConfig instance for this event.
    :returns: Tuple of (order_fact_data: dict, ticket_facts_data: list of dicts).
    """
    positions = list(order.positions.all())
    main_positions = [p for p in positions if p.addon_to_id is None]

    # ── Financials ────────────────────────────────────────────────────────────
    total_gross = order.total or Decimal("0")
    total_net = sum(
        (p.net_price if hasattr(p, "net_price") else p.price) for p in positions
    )
    tax_amount = total_gross - total_net

    # ── Payment info ─────────────────────────────────────────────────────────
    confirmed_payment = (
        order.payments.filter(state="confirmed").order_by("payment_date").last()
    )
    payment_provider = confirmed_payment.provider if confirmed_payment else ""
    payment_datetime = confirmed_payment.payment_date if confirmed_payment else None

    # ── Order structure ───────────────────────────────────────────────────────
    ticket_count = len(main_positions)
    attendee_emails = {
        p.attendee_email.lower().strip()
        for p in positions
        if p.attendee_email and p.attendee_email.strip()
    }
    unique_attendee_count = len(attendee_emails) if attendee_emails else ticket_count
    is_group_order = ticket_count > 1

    # ── Geography ─────────────────────────────────────────────────────────────
    country_code = resolve_country(order)
    city, postal_code = resolve_city_and_postal(order)

    # ── Demographics ──────────────────────────────────────────────────────────
    age_range = resolve_age_range(order)
    is_age_confirmed = resolve_age_confirmed(order)

    # ── Caravan ───────────────────────────────────────────────────────────────
    has_caravan_pass, camper_van_length_bucket = resolve_caravan_data(order)

    # ── Repeat hashes ─────────────────────────────────────────────────────────
    # We collect hashes for all unique attendee emails, plus the main order email.
    emails_to_hash = attendee_emails.copy()
    if order.email:
        emails_to_hash.add(order.email.lower().strip())
    
    repeat_hashes = [generate_repeat_hash(em) for em in emails_to_hash if em]
    # Keep the primary order email hash for the DB `repeat_hash` column to avoid schema changes
    primary_email = (order.email or "").strip()
    primary_repeat_hash = generate_repeat_hash(primary_email) if primary_email else ""

    # ── Early buyer ───────────────────────────────────────────────────────────
    bought_early = False
    if order.event.date_from:
        bought_early = order.datetime <= (order.event.date_from - timedelta(days=30))

    # ── Local buyer ───────────────────────────────────────────────────────────
    home_country = (config.home_country or "").upper()
    is_local_buyer = bool(home_country) and country_code == home_country

    # ── Refund status ─────────────────────────────────────────────────────────
    is_refunded = order.refunds.filter(
        state__in=("done", "transit")
    ).exists()

    # ── Series / edition context ──────────────────────────────────────────────
    series_slug = config.series.slug if config.series else ""
    edition_year = config.edition_year

    order_fact_data = {
        "organizer_id": order.event.organizer_id,
        "series_slug": series_slug,
        "edition_year": edition_year,
        "order_code": order.code,
        "order_datetime": order.datetime,
        "payment_datetime": payment_datetime,
        "order_status": order.status,
        "total_gross": total_gross,
        "total_net": total_net,
        "tax_amount": tax_amount,
        "currency": order.event.currency,
        "ticket_count": ticket_count,
        "unique_attendee_count": unique_attendee_count,
        "is_group_order": is_group_order,
        "payment_provider": payment_provider,
        "is_refunded": is_refunded,
        "country_code": country_code if country_code != "UNKNOWN" else "",
        "city": city,
        "postal_code": postal_code,
        "age_range": age_range,
        "is_age_confirmed": is_age_confirmed,
        "language": order.locale or "",
        "has_caravan_pass": has_caravan_pass,
        "camper_van_length_bucket": camper_van_length_bucket,
        "repeat_hash": primary_repeat_hash,
        # Repeat status fields are filled in by repeat_detector after this call
        "is_repeat_buyer": False,
        "repeat_from_last_edition": False,
        "repeat_from_any_previous": False,
        "repeat_count": 0,
        "first_seen_edition_year": None,
        "checkin_completed": False,
        # Prediction filled in after repeat status
        "predicted_repeat_probability": 0,
        # Extra non-model fields used downstream
        "_bought_early": bought_early,
        "_is_local_buyer": is_local_buyer,
        "_repeat_hashes": repeat_hashes,
        "_identities": _extract_identities(order, positions),
    }

    # ── Ticket-level facts ────────────────────────────────────────────────────
    ticket_facts_data: List[Dict] = []
    for position in positions:
        item = position.item
        variation = position.variation

        pos_has_caravan, pos_van_bucket = resolve_caravan_data_for_position(position)
        pos_age_range = _position_age_range(position)
        pos_age_confirmed = _position_age_confirmed(position)

        # net_price may not be a direct attribute on all Pretix versions
        net_price = getattr(position, "net_price", position.price)

        ticket_facts_data.append(
            {
                "item_id": item.pk,
                "item_name": str(item.name),
                "variation_id": variation.pk if variation else None,
                "variation_name": str(variation.value) if variation else "",
                "price": position.price,
                "tax_rate": position.tax_rate if hasattr(position, "tax_rate") else Decimal("0"),
                "net_price": net_price,
                "is_addon": position.addon_to_id is not None,
                "age_range": pos_age_range,
                "is_age_confirmed": pos_age_confirmed,
                "is_caravan_pass": pos_has_caravan,
                "camper_van_length_bucket": pos_van_bucket,
            }
        )

    return order_fact_data, ticket_facts_data


# ── Per-position helpers ──────────────────────────────────────────────────────

def _position_age_range(position) -> str:
    """Age range for a single position's answers."""
    from .age_bucketer import _is_birth_question, _parse_birthdate, _age_to_bucket
    from datetime import date

    today = date.today()
    for answer in position.answers.all():
        if not _is_birth_question(str(answer.question.question)):
            continue
        if not answer.answer:
            continue
        bd = _parse_birthdate(answer.answer)
        if bd:
            age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
            return _age_to_bucket(age)
    return ""


def _position_age_confirmed(position):
    """18+ confirmation for a single position's answers."""
    _CONFIRM_KEYWORDS = ("18", "legal guardian", "volljährig", "maggiorenne")
    for answer in position.answers.all():
        qt = str(answer.question.question).lower()
        if any(kw in qt for kw in _CONFIRM_KEYWORDS):
            ans = answer.answer.lower().strip()
            if ans in ("true", "yes", "1", "ja", "si", "sì", "oui"):
                return True
            if ans in ("false", "no", "0"):
                return False
    return None

def _extract_identities(order, positions) -> List[Dict]:
    """
    Extracts high-confidence and medium-confidence identity keys from an order.
    Returns a list of dicts: {'type': str, 'hash': str}
    """
    from .hash_service import generate_repeat_hash
    identities = []

    # 1. Email identity (base strategy fallback)
    if order.email:
        identities.append({
            "type": "email",
            "hash": generate_repeat_hash(order.email.lower().strip())
        })

    # 2. Payment provider fingerprints (High Confidence)
    confirmed_payment = order.payments.filter(state="confirmed").order_by("payment_date").last()
    if confirmed_payment and confirmed_payment.info_data:
        info = confirmed_payment.info_data
        provider = confirmed_payment.provider

        if provider == "stripe":
            # Extract Stripe underlying credit card fingerprint (consistent across different cards attached to same account)
            # or the specific payment method fingerprint.
            fingerprint = info.get("payment_method_details", {}).get("card", {}).get("fingerprint")
            if fingerprint:
                identities.append({
                    "type": "stripe_card",
                    "hash": generate_repeat_hash(fingerprint)
                })
        
        elif provider == "paypal":
            payer_id = info.get("payer", {}).get("payer_info", {}).get("payer_id")
            if payer_id:
                identities.append({
                    "type": "paypal_payer",
                    "hash": generate_repeat_hash(payer_id)
                })
        
        elif provider == "banktransfer":
            iban = info.get("iban")
            if iban:
                # Strip spaces/formatting from IBAN
                clean_iban = str(iban).replace(" ", "").upper()
                identities.append({
                    "type": "bank_iban",
                    "hash": generate_repeat_hash(clean_iban)
                })

    # 3. Composite Demographic Identity (Name + DOB)
    from .age_bucketer import _is_birth_question, _parse_birthdate
    
    for pos in positions:
        name = pos.attendee_name
        if not name:
            continue
            
        dob = None
        for answer in pos.answers.all():
            if _is_birth_question(str(answer.question.question)):
                parsed_dob = _parse_birthdate(answer.answer)
                if parsed_dob:
                    dob = parsed_dob.strftime("%Y-%m-%d")
                break
                
        if name and dob:
            # Combine them for a highly specific composite identity
            composite_string = f"{name.strip().lower()}_{dob}"
            identities.append({
                "type": "name_dob",
                "hash": generate_repeat_hash(composite_string)
            })

    # Filter out duplicates while preserving dict structure
    seen = set()
    unique_identities = []
    for id_dict in identities:
        key = (id_dict["type"], id_dict["hash"])
        if key not in seen:
            seen.add(key)
            unique_identities.append(id_dict)

    return unique_identities
