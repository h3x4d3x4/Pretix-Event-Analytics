"""
Single write path for analytics facts.

Both live ingestion (Celery tasks fired by Pretix signals) and bulk resync
go through ``write_order`` so there is exactly one place that turns a Pretix
order into fact rows.

READ-ONLY AGAINST PRETIX CORE
-----------------------------
This module only *reads* Pretix models. It writes exclusively to the
plugin's own tables.
"""
import logging
from typing import Dict, Iterable, List, Optional

from django.db import transaction
from django.db.models import Min, Prefetch

logger = logging.getLogger(__name__)


def _order_prefetches():
    from pretix.base.models import QuestionAnswer

    answers = Prefetch(
        "answers",
        queryset=QuestionAnswer.objects.select_related("question").prefetch_related("options"),
    )
    return [
        "positions__item__category",
        "positions__variation",
        "positions__voucher",
        Prefetch("positions__answers", queryset=answers.queryset),
        "fees",
        "payments",
        "refunds",
    ]


def load_orders(pks: Iterable[int]) -> List:
    """Load orders with everything the normalizer touches, in one batch."""
    from django_scopes import scopes_disabled
    from pretix.base.models import Order

    with scopes_disabled():
        return list(
            Order.objects.filter(pk__in=list(pks))
            .select_related("event__organizer", "invoice_address")
            .prefetch_related(*_order_prefetches())
        )


def load_checkins(order_pks: Iterable[int]) -> Dict[int, object]:
    """{position_id: first successful entry scan} for the given orders."""
    from django_scopes import scopes_disabled
    from pretix.base.models import Checkin

    with scopes_disabled():
        rows = (
            Checkin.all.filter(
                successful=True,
                type=Checkin.TYPE_ENTRY,
                position__order_id__in=list(order_pks),
            )
            .values("position_id")
            .annotate(first=Min("datetime"))
        )
        return {r["position_id"]: r["first"] for r in rows}


def write_order(order, config, *, checkins: Optional[Dict[int, object]] = None, quick_repeat: bool = True):
    """
    Normalize ``order`` and upsert its fact rows.

    ``quick_repeat`` runs an immediate backward-looking repeat lookup so the
    dashboard is roughly right straight away; the series-wide resolver
    (services.people) later replaces it with the exact answer.

    :raises ValueError: when the order cannot be normalized (skip, no retry).
    :returns: the AnalyticsOrderFact.
    """
    from ..models import (
        FACT_VERSION,
        AnalyticsAnswerFact,
        AnalyticsIdentity,
        AnalyticsOrderFact,
        AnalyticsTicketFact,
    )
    from .normalizer import normalize_order
    from .predictor import score_from_fact
    from .repeat_detector import evaluate_repeat_status

    if checkins is None:
        checkins = load_checkins([order.pk])

    order_data, tickets_data = normalize_order(
        order, config, checkins=checkins, tracked_question_ids=config.tracked_question_ids,
    )
    buyer_ids = order_data.pop("_identities")
    order_data["fact_version"] = FACT_VERSION

    with transaction.atomic():
        existing = (
            AnalyticsOrderFact.objects.select_for_update()
            .filter(event=order.event, order_code=order.code)
            .first()
        )
        if quick_repeat:
            # Buyers are identified by the order e-mail only (payment
            # methods are shared with friends and family).
            lookup = [i for i in buyer_ids if i["type"] == "email"]
            order_data.update(evaluate_repeat_status(order.event, lookup))
        elif existing:
            # Keep series-resolved values until the resolver runs again.
            for f in ("is_repeat_buyer", "repeat_from_last_edition", "repeat_from_any_previous",
                      "repeat_count", "first_seen_edition_year", "person_key", "editions_attended"):
                order_data[f] = getattr(existing, f)

        order_data["predicted_repeat_probability"] = score_from_fact(order_data)

        if existing:
            for k, v in order_data.items():
                setattr(existing, k, v)
            existing.save()
            fact = existing
            # Children are rebuilt from scratch (cascade removes identities/answers).
            AnalyticsTicketFact.objects.filter(order_fact=fact).delete()
            AnalyticsIdentity.objects.filter(order_fact=fact).delete()
        else:
            fact = AnalyticsOrderFact.objects.create(event=order.event, **order_data)

        ticket_rows, ticket_ids, ticket_answers = [], [], []
        for t in tickets_data:
            ids = t.pop("_identities")
            answers = t.pop("_answers")
            ticket_rows.append(AnalyticsTicketFact(order_fact=fact, event=order.event, **t))
            ticket_ids.append(ids)
            ticket_answers.append(answers)
        created = AnalyticsTicketFact.objects.bulk_create(ticket_rows)

        identity_rows = [
            AnalyticsIdentity(order_fact=fact, event=order.event, identity_type=i["type"], identity_hash=i["hash"])
            for i in buyer_ids
        ]
        answer_rows = []
        for tf, ids, answers in zip(created, ticket_ids, ticket_answers):
            identity_rows.extend(
                AnalyticsIdentity(order_fact=fact, ticket_fact=tf, event=order.event,
                                  identity_type=i["type"], identity_hash=i["hash"])
                for i in ids
            )
            answer_rows.extend(AnalyticsAnswerFact(ticket_fact=tf, event=order.event, **a) for a in answers)
        AnalyticsIdentity.objects.bulk_create(identity_rows, ignore_conflicts=True)
        if answer_rows:
            AnalyticsAnswerFact.objects.bulk_create(answer_rows)

    return fact


def is_ingestible(order) -> bool:
    """
    Orders we keep facts for: paid ones, and canceled ones that had been
    paid at some point (so refunds and cancellations stay measurable).
    Never-paid expired/canceled reservations are ignored.
    """
    if order.status == "p":
        return True
    if order.status == "c":
        return any(p.state in ("confirmed", "refunded") for p in order.payments.all()) or any(
            r.state in ("done", "transit") for r in order.refunds.all()
        )
    return False
