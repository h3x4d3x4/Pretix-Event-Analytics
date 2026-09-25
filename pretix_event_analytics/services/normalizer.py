"""
Order normalizer — the main orchestration service.

Transforms a raw Pretix Order into the data needed to populate
AnalyticsOrderFact, AnalyticsTicketFact, AnalyticsIdentity and
AnalyticsAnswerFact. Calls all sub-services and returns plain dicts.

This module never writes: it returns plain dicts so that callers
(services.ingest) control when DB writes happen. It reads Pretix models
only to fetch canceled positions/fees, which the prefetch cannot supply.

Identity ownership
------------------
Identity signals are split by who they describe:

* buyer-level (``order_identities``): the order e-mail and payment
  fingerprints (card, PayPal payer, IBAN);
* attendee-level (``ticket["_identities"]``): attendee e-mail and
  name + date of birth of each admission position.

Keeping them apart is what allows a group order for five friends to count
as five people instead of merging everyone into the buyer.
"""
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from .age_bucketer import (
    _age_to_bucket,
    _is_birth_question,
    _parse_birthdate,
    age_on,
    is_age_confirm_question,
    parse_yes_no,
    resolve_age_confirmed,
    resolve_age_range,
)
from .country_resolver import last_confirmed_payment, resolve_city_and_postal, resolve_country
from .hash_service import generate_repeat_hash
from .van_length_bucketer import resolve_caravan_data, resolve_caravan_data_for_position

# Question types whose answers are safe to aggregate (fixed option sets).
AGGREGATABLE_QUESTION_TYPES = ("B", "C", "M")


def order_positions(order) -> list:
    """
    Positions that represent what the order *is*: active positions for live
    orders, and the (canceled) positions for canceled orders so we still
    know what was refunded.

    Canceled positions are queried directly: prefetching ``positions`` fills
    the ``all_positions`` cache with active rows only, so it cannot be used.
    """
    if order.status == "c":
        from django_scopes import scopes_disabled
        from pretix.base.models import OrderPosition

        with scopes_disabled():
            return list(
                OrderPosition.all.filter(order_id=order.pk)
                .select_related("item__category", "variation", "voucher")
                .prefetch_related("answers__question", "answers__options")
                .order_by("pk")
            )
    return list(order.positions.all())


def order_fees(order) -> list:
    if order.status == "c":
        from django_scopes import scopes_disabled
        from pretix.base.models import OrderFee

        with scopes_disabled():
            return list(OrderFee.all.filter(order_id=order.pk))
    return list(order.fees.all())


def event_local_date(event, dt):
    if dt is None:
        return None
    return dt.astimezone(event.timezone).date()


def normalize_order(order, config, *, checkins: Optional[Dict[int, object]] = None,
                    tracked_question_ids=None) -> Tuple[Dict, List[Dict]]:
    """
    Normalize a Pretix Order into order-level and ticket-level fact dicts.

    :param order: Pretix Order instance with related data prefetched.
    :param config: EventAnalyticsConfig instance for this event.
    :param checkins: {position_id: first successful entry datetime}.
    :param tracked_question_ids: question ids whose answers are aggregated.
    :returns: (order_fact_data, ticket_facts_data). ``order_fact_data`` carries
              the private key ``_identities`` (buyer-level); every ticket dict
              carries ``_identities`` and ``_answers``.
    """
    checkins = checkins or {}
    tracked = set(tracked_question_ids or [])
    event = order.event
    positions = order_positions(order)
    main_positions = [p for p in positions if p.addon_to_id is None]

    if not positions:
        # An order with zero positions is either malformed or an empty
        # reservation. Bail out rather than writing a degenerate fact row.
        raise ValueError(f"order {order.code} has no positions")

    event_date = event_local_date(event, event.date_from)
    order_date = event_local_date(event, order.datetime)

    # ── Financials ────────────────────────────────────────────────────────────
    fees = order_fees(order)
    fees_total = sum((f.value for f in fees), Decimal("0.00"))
    positions_gross = sum((p.price for p in positions), Decimal("0.00"))
    if order.status == "c":
        # A canceled order's total is reduced to any cancellation fee; keep
        # the original value so lost revenue stays measurable.
        total_gross = positions_gross + fees_total
    else:
        total_gross = order.total if order.total is not None else positions_gross + fees_total
    tax_amount = sum((p.tax_value or 0 for p in positions), Decimal("0.00")) + sum(
        (f.tax_value or 0 for f in fees), Decimal("0.00")
    )
    total_net = total_gross - tax_amount

    done_refunds = [r for r in order.refunds.all() if r.state in ("done", "transit")]
    refunded_amount = sum((r.amount for r in done_refunds), Decimal("0.00"))
    # Fully refunded (or canceled with money returned) orders are excluded
    # from default dashboards; partial refunds only reduce refunded_amount.
    is_refunded = bool(done_refunds) and (order.status == "c" or refunded_amount >= total_gross)
    canceled_at = None
    if order.status == "c":
        canceled_at = getattr(order, "cancellation_date", None) or (
            max((r.execution_date or r.created for r in done_refunds), default=None)
        )

    # ── Payment info ─────────────────────────────────────────────────────────
    confirmed_payment = last_confirmed_payment(order)
    payment_provider = confirmed_payment.provider if confirmed_payment else ""
    payment_datetime = confirmed_payment.payment_date if confirmed_payment else None

    # ── Order structure ───────────────────────────────────────────────────────
    ticket_count = max(len(main_positions), 1)
    addon_count = len(positions) - len(main_positions)
    buyer_email = (order.email or "").strip().lower()
    attendee_emails = {
        p.attendee_email.lower().strip()
        for p in main_positions
        if p.attendee_email and p.attendee_email.strip()
    }
    unique_attendee_count = max(len(attendee_emails) if attendee_emails else ticket_count, 1)

    # ── Geography / demographics ──────────────────────────────────────────────
    country_code = resolve_country(order, positions=positions, payment=confirmed_payment)
    city, postal_code = resolve_city_and_postal(order)
    age_range = resolve_age_range(order, positions=positions, ref_date=event_date)
    is_age_confirmed = resolve_age_confirmed(order, positions=positions)
    has_caravan_pass, camper_van_length_bucket = resolve_caravan_data(order, positions=positions)

    days_before_event = (event_date - order_date).days if event_date and order_date else None
    home_country = (config.home_country or "").upper()
    resolved_country = country_code if country_code != "UNKNOWN" else ""
    is_local_buyer = bool(home_country) and resolved_country == home_country

    series_slug = config.series.slug if config.series else ""

    order_fact_data = {
        "organizer_id": event.organizer_id,
        "series_slug": series_slug,
        "edition_year": config.edition_year,
        "order_code": order.code,
        "order_datetime": order.datetime,
        "payment_datetime": payment_datetime,
        "order_status": order.status,
        "total_gross": total_gross,
        "total_net": total_net,
        "tax_amount": tax_amount,
        "fees_total": fees_total,
        "refunded_amount": refunded_amount,
        "canceled_at": canceled_at,
        "currency": event.currency,
        "ticket_count": ticket_count,
        "unique_attendee_count": unique_attendee_count,
        "is_group_order": ticket_count > 1,
        "has_addons": addon_count > 0,
        "addon_count": addon_count,
        "voucher_used": any(p.voucher_id for p in positions),
        "days_before_event": days_before_event,
        "payment_provider": payment_provider,
        "is_refunded": is_refunded,
        "country_code": resolved_country,
        "city": city,
        "postal_code": postal_code,
        "age_range": age_range,
        "is_age_confirmed": is_age_confirmed,
        "language": order.locale or "",
        "has_caravan_pass": has_caravan_pass,
        "camper_van_length_bucket": camper_van_length_bucket,
        "repeat_hash": generate_repeat_hash(buyer_email),
        "is_local_buyer": is_local_buyer,
        "checkin_completed": any(p.pk in checkins for p in main_positions),
        "_identities": buyer_identities(order, confirmed_payment),
    }

    # ── Ticket-level facts ────────────────────────────────────────────────────
    single_ticket = len(main_positions) == 1
    ticket_facts_data: List[Dict] = []
    for position in positions:
        item = position.item
        variation = position.variation
        is_addon = position.addon_to_id is not None
        pos_has_caravan, pos_van_bucket = resolve_caravan_data_for_position(position)
        voucher = position.voucher if position.voucher_id else None

        identities: List[Dict] = []
        attendee_is_buyer = False
        if not is_addon:
            identities = attendee_identities(position)
            att_email = (position.attendee_email or "").strip().lower()
            # The buyer holds this ticket when they put their own address on
            # it, or when a single-ticket order carries no attendee address.
            attendee_is_buyer = bool(buyer_email) and (
                att_email == buyer_email or (not att_email and single_ticket)
            )

        category = getattr(item, "category", None)
        ticket_facts_data.append(
            {
                "position_id": position.pk,
                "item_id": item.pk,
                "item_name": str(item.name),
                "item_category": str(category.name) if category else "",
                "variation_id": variation.pk if variation else None,
                "variation_name": str(variation.value) if variation else "",
                "price": position.price,
                "tax_rate": position.tax_rate or Decimal("0"),
                "net_price": position.price - (position.tax_value or Decimal("0")),
                "is_addon": is_addon,
                "voucher_code": voucher.code if voucher else "",
                "voucher_tag": (voucher.tag or "") if voucher else "",
                "age_range": _position_age_range(position, event_date),
                "is_age_confirmed": _position_age_confirmed(position),
                "is_caravan_pass": pos_has_caravan,
                "camper_van_length_bucket": pos_van_bucket,
                "checked_in": position.pk in checkins,
                "first_checkin_at": checkins.get(position.pk),
                "attendee_is_buyer": attendee_is_buyer,
                "attendee_identified": bool(identities) or attendee_is_buyer,
                "_identities": identities,
                "_answers": _tracked_answers(position, tracked) if tracked else [],
            }
        )

    return order_fact_data, ticket_facts_data


# ── Per-position helpers ──────────────────────────────────────────────────────

def _position_birthdate(position):
    for answer in position.answers.all():
        if not _is_birth_question(str(answer.question.question)):
            continue
        if not answer.answer:
            continue
        bd = _parse_birthdate(answer.answer)
        if bd:
            return bd
    return None


def _position_age_range(position, ref_date) -> str:
    """Age bucket for a single position, measured on the event date."""
    from datetime import date

    bd = _position_birthdate(position)
    if not bd:
        return ""
    age = age_on(bd, ref_date or date.today())
    if age < 0 or age > 120:
        return ""
    return _age_to_bucket(age)


def _position_age_confirmed(position):
    """18+ confirmation for a single position's answers."""
    for answer in position.answers.all():
        if not is_age_confirm_question(str(answer.question.question)):
            continue
        parsed = parse_yes_no(answer.answer)
        if parsed is not None:
            return parsed
    return None


def _tracked_answers(position, tracked: set) -> List[Dict]:
    out = []
    for answer in position.answers.all():
        q = answer.question
        if q.pk not in tracked or q.type not in AGGREGATABLE_QUESTION_TYPES:
            continue
        label = str(q.question)[:255]
        if q.type == "B":
            parsed = parse_yes_no(answer.answer)
            if parsed is None:
                continue
            out.append({"question_id": q.pk, "question_label": label, "answer_value": "Yes" if parsed else "No"})
        else:
            for opt in answer.options.all():
                out.append({"question_id": q.pk, "question_label": label, "answer_value": str(opt.answer)[:255]})
    return out


def _dedupe(identities: List[Dict]) -> List[Dict]:
    seen = set()
    out = []
    for i in identities:
        key = (i["type"], i["hash"])
        if i["hash"] and key not in seen:
            seen.add(key)
            out.append(i)
    return out


def buyer_identities(order, confirmed_payment) -> List[Dict]:
    """Buyer-level identity keys: order e-mail + payment fingerprints."""
    identities = []

    if order.email:
        identities.append({"type": "email", "hash": generate_repeat_hash(order.email.lower().strip())})

    if confirmed_payment and confirmed_payment.info_data:
        info = confirmed_payment.info_data
        provider = confirmed_payment.provider

        if provider == "stripe":
            fingerprint = info.get("payment_method_details", {}).get("card", {}).get("fingerprint")
            if fingerprint:
                identities.append({"type": "stripe_card", "hash": generate_repeat_hash(fingerprint)})
        elif provider == "paypal":
            payer_id = info.get("payer", {}).get("payer_info", {}).get("payer_id")
            if payer_id:
                identities.append({"type": "paypal_payer", "hash": generate_repeat_hash(payer_id)})
        elif provider == "banktransfer":
            iban = info.get("iban")
            if iban:
                clean_iban = str(iban).replace(" ", "").upper()
                identities.append({"type": "bank_iban", "hash": generate_repeat_hash(clean_iban)})

    return _dedupe(identities)


def attendee_identities(position) -> List[Dict]:
    """Attendee-level identity keys: attendee e-mail + name/DOB composite."""
    identities = []
    if position.attendee_email and position.attendee_email.strip():
        identities.append({"type": "email", "hash": generate_repeat_hash(position.attendee_email.lower().strip())})

    name = (position.attendee_name or "").strip().lower()
    bd = _position_birthdate(position)
    if name and bd:
        identities.append({"type": "name_dob", "hash": generate_repeat_hash(f"{name}_{bd.strftime('%Y-%m-%d')}")})
    return _dedupe(identities)
