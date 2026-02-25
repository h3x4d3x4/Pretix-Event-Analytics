"""
Management command: generate_test_data

Injects realistic fake AnalyticsOrderFact and AnalyticsTicketFact rows for
a specific Pretix event so you can preview the analytics dashboard without
running real orders through the ingestion pipeline.

Usage:
    # Basic — 150 fake orders for one event
    python -m pretix generate_test_data --organizer suti --event suti-festival-2024

    # Full 4-edition scenario (run once per event, same buyer pool ensures cohort data)
    python -m pretix generate_test_data --organizer suti --event suti-festival-2022 --edition-year 2022 --orders 80
    python -m pretix generate_test_data --organizer suti --event suti-festival-2023 --edition-year 2023 --orders 120
    python -m pretix generate_test_data --organizer suti --event suti-festival-2024 --edition-year 2024 --orders 160
    python -m pretix generate_test_data --organizer suti --event suti-festival-2026 --edition-year 2026 --orders 200

Options:
    --orders N          Number of fake orders to generate (default: 150)
    --edition-year YYYY Override the edition year (default: inferred from event.date_from)
    --series SLUG       Series slug to use (default: suti-festival)
    --checkin           Mark ~80%% of orders as checked in
    --clear             Delete existing fact data before generating
"""
import hashlib
import hmac
import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


# ── Fake data pools ───────────────────────────────────────────────────────────

COUNTRIES = [
    ("RO", 42), ("DE", 14), ("HU", 8), ("GB", 7), ("AT", 5),
    ("FR", 5), ("NL", 4), ("IT", 4), ("BE", 3), ("CH", 3),
    ("CZ", 2), ("SK", 2), ("PL", 1),
]

AGE_RANGES = [
    ("18-24", 18), ("25-34", 28), ("35-44", 22),
    ("45-54", 16), ("55-64", 10), ("65+", 4), ("0-17", 2),
]

VAN_LENGTHS = [("<6m", 35), ("6-8m", 45), (">8m", 20)]

TICKET_TYPES = [
    {"name": "General Admission", "price": Decimal("89.00"), "weight": 60},
    {"name": "VIP Weekend Pass",   "price": Decimal("180.00"), "weight": 15},
    {"name": "Day Pass — Friday",  "price": Decimal("45.00"),  "weight": 10},
    {"name": "Day Pass — Saturday","price": Decimal("45.00"),  "weight": 10},
    {"name": "Workshop Pass",      "price": Decimal("35.00"),  "weight": 5},
]

CARAVAN_TICKET = {
    "name": "Camper Van Pass", "price": Decimal("60.00"), "weight": 100
}

PAYMENT_PROVIDERS = [
    ("stripe", 55), ("paypal", 25), ("banktransfer", 12), ("cash", 8),
]

CITIES_BY_COUNTRY = {
    "RO": ["București", "Cluj-Napoca", "Timișoara", "Iași", "Constanța"],
    "DE": ["Berlin", "München", "Hamburg", "Frankfurt", "Köln"],
    "HU": ["Budapest", "Debrecen", "Pécs", "Győr", ""],
    "GB": ["London", "Manchester", "Birmingham", "Leeds", ""],
    "AT": ["Wien", "Graz", "Linz", "", ""],
    "FR": ["Paris", "Lyon", "Marseille", "", ""],
    "NL": ["Amsterdam", "Rotterdam", "Den Haag", "", ""],
    "IT": ["Roma", "Milano", "Napoli", "", ""],
}


def _weighted_choice(pool, rng):
    """Pick a value from [(value, weight), ...] using weights."""
    choices = [v for v, w in pool for _ in range(w)]
    return rng.choice(choices)


def _fake_hash(email: str, salt: str = "test-salt") -> str:
    """Deterministic HMAC-SHA256 hash — mirrors hash_service.py."""
    return hmac.new(
        salt.encode("utf-8"),
        email.lower().strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _random_order_code(rng) -> str:
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(rng.choices(chars, k=5))


class Command(BaseCommand):
    help = "Generate fake analytics test data for a configured event."

    def add_arguments(self, parser):
        parser.add_argument(
            "--organizer", required=True, metavar="ORGANIZER_SLUG",
            help="Organizer slug (must already exist).",
        )
        parser.add_argument(
            "--event", required=True, metavar="EVENT_SLUG",
            help="Event slug (must already exist).",
        )
        parser.add_argument(
            "--orders", type=int, default=150, metavar="N",
            help="Number of fake orders to generate (default: 150).",
        )
        parser.add_argument(
            "--edition-year", type=int, dest="edition_year", metavar="YYYY",
            help="Edition year override (default: inferred from event.date_from).",
        )
        parser.add_argument(
            "--series", default="suti-festival", metavar="SERIES_SLUG",
            help="Series slug to link to (default: suti-festival).",
        )
        parser.add_argument(
            "--checkin", action="store_true",
            help="Mark ~80%% of orders as checked in.",
        )
        parser.add_argument(
            "--clear", action="store_true",
            help="Delete existing fact data for this event first.",
        )

    def handle(self, *args, **options):
        from pretix.base.models import Event, Organizer

        from ...models import (
            AnalyticsOrderFact,
            AnalyticsTicketFact,
            EventAnalyticsConfig,
            EventSeries,
        )

        # ── Resolve organizer + event ─────────────────────────────────────────
        from django_scopes import scopes_disabled

        try:
            organizer = Organizer.objects.get(slug=options["organizer"])
        except Organizer.DoesNotExist:
            raise CommandError(f"Organizer '{options['organizer']}' not found.")

        with scopes_disabled():
            try:
                event = Event.objects.get(slug=options["event"], organizer=organizer)
            except Event.DoesNotExist:
                raise CommandError(
                    f"Event '{options['event']}' not found under organizer '{options['organizer']}'."
                )

        # ── Determine edition year ────────────────────────────────────────────
        edition_year = options.get("edition_year")
        if not edition_year:
            if event.date_from:
                edition_year = event.date_from.year
            else:
                raise CommandError(
                    "Cannot infer edition year: event has no date_from. "
                    "Pass --edition-year explicitly."
                )

        # ── Create/get series + config ────────────────────────────────────────
        series, series_created = EventSeries.objects.get_or_create(
            organizer=organizer,
            slug=options["series"],
            defaults={"name": "Suti Festival"},
        )
        if series_created:
            self.stdout.write(f"  Created series '{series.name}' (slug: {series.slug})")

        config, _ = EventAnalyticsConfig.objects.update_or_create(
            event=event,
            defaults={
                "series": series,
                "edition_year": edition_year,
                "home_country": "RO",
                "is_active": True,
            },
        )
        self.stdout.write(
            f"  Linked {event.slug} → {series.name} (edition {edition_year})"
        )

        # ── Optionally clear existing data ────────────────────────────────────
        if options["clear"]:
            deleted, _ = AnalyticsOrderFact.objects.filter(event=event).delete()
            self.stdout.write(f"  Cleared {deleted} existing fact rows.")

        # ── Deterministic RNG seeded by event slug ────────────────────────────
        rng = random.Random(f"analytics-test-{event.slug}-{edition_year}")

        # ── Shared buyer pool (enables cross-edition repeat detection) ─────────
        # Pool of 400 fake buyer emails shared across all editions.
        # When the same pool index appears in two different editions, that buyer
        # will be detected as a repeat buyer.
        POOL_SIZE = 400
        buyer_pool = [f"buyer_{i:04d}@example.com" for i in range(POOL_SIZE)]

        n_orders = options["orders"]
        include_checkin = options["checkin"]

        # Most buyers come from the shared pool; ~15% are unique to this edition
        shared_buyers = int(n_orders * 0.85)
        unique_buyers = n_orders - shared_buyers
        buyer_indices = (
            rng.choices(range(POOL_SIZE), k=shared_buyers)
            + [POOL_SIZE + i for i in range(unique_buyers)]
        )
        rng.shuffle(buyer_indices)

        # ── Event date window ─────────────────────────────────────────────────
        if event.date_from:
            event_dt = event.date_from
        else:
            import datetime
            event_dt = timezone.make_aware(
                datetime.datetime(edition_year, 7, 15, 14, 0)
            )

        # Spread orders over the 9 months before the event
        sale_start = event_dt - timedelta(days=270)

        # ── Generate facts ────────────────────────────────────────────────────
        order_facts = []
        ticket_fact_rows = []

        self.stdout.write(f"  Generating {n_orders} fake orders for {event.slug}…")

        used_codes = set()

        for idx in buyer_indices:
            # Order code
            while True:
                code = _random_order_code(rng)
                if code not in used_codes:
                    used_codes.add(code)
                    break

            # Email → hash
            email = buyer_pool[idx] if idx < POOL_SIZE else f"unique_{idx}@example.com"
            repeat_hash = _fake_hash(email)

            # Order datetime — bell-curve weighted toward 60 days before event
            days_before = int(rng.betavariate(2, 5) * 270)
            order_dt = event_dt - timedelta(
                days=days_before,
                hours=rng.randint(0, 23),
                minutes=rng.randint(0, 59),
            )
            if order_dt < sale_start:
                order_dt = sale_start + timedelta(days=rng.randint(0, 3))

            payment_dt = order_dt + timedelta(minutes=rng.randint(1, 30))

            # Demographics
            country = _weighted_choice(COUNTRIES, rng)
            age_range = _weighted_choice(AGE_RANGES, rng)
            is_age_confirmed = age_range not in ("0-17",)
            language = "ro" if country == "RO" else "en"
            city = rng.choice(CITIES_BY_COUNTRY.get(country, [""]))
            postal_code = ""

            # Caravan pass (~18% of orders)
            has_caravan = rng.random() < 0.18
            van_bucket = _weighted_choice(VAN_LENGTHS, rng) if has_caravan else ""

            # Tickets
            main_ticket = _weighted_choice(
                [(t, t["weight"]) for t in TICKET_TYPES], rng
            )
            is_group = rng.random() < 0.12
            extra_tickets = rng.randint(1, 3) if is_group else 0
            ticket_count = 1 + extra_tickets

            total_gross = main_ticket["price"] * ticket_count
            if has_caravan:
                total_gross += CARAVAN_TICKET["price"]
            # Small random variance ±10%
            variance = Decimal(str(round(rng.uniform(0.9, 1.1), 2)))
            total_gross = (total_gross * variance).quantize(Decimal("0.01"))
            total_net = (total_gross / Decimal("1.19")).quantize(Decimal("0.01"))
            tax_amount = total_gross - total_net

            payment_provider = _weighted_choice(PAYMENT_PROVIDERS, rng)
            is_refunded = rng.random() < 0.04  # 4% refund rate
            checkin_done = include_checkin and not is_refunded and rng.random() < 0.82

            # Predictive score (simplified, mirrors predictor.py)
            score = 0
            # (repeat status determined later from DB; set 0 for initial gen)
            if has_caravan:
                score += 5
            if days_before > 180:
                score += 15  # bought early
            if ticket_count > 2:
                score += 10
            if country == "RO":
                score += 10
            if is_group:
                score += 5
            if checkin_done:
                score += 20

            order_fact = {
                "organizer_id": organizer.pk,
                "series_slug": series.slug,
                "edition_year": edition_year,
                "order_code": code,
                "order_datetime": order_dt,
                "payment_datetime": payment_dt,
                "order_status": "p",
                "total_gross": total_gross,
                "total_net": total_net,
                "tax_amount": tax_amount,
                "currency": event.currency or "EUR",
                "ticket_count": ticket_count,
                "unique_attendee_count": ticket_count,
                "is_group_order": is_group,
                "payment_provider": payment_provider,
                "is_refunded": is_refunded,
                "country_code": country,
                "city": city,
                "postal_code": postal_code,
                "age_range": age_range,
                "is_age_confirmed": is_age_confirmed,
                "language": language,
                "has_caravan_pass": has_caravan,
                "camper_van_length_bucket": van_bucket,
                "repeat_hash": repeat_hash,
                "is_repeat_buyer": False,   # filled after DB scan
                "repeat_from_last_edition": False,
                "repeat_from_any_previous": False,
                "repeat_count": 0,
                "first_seen_edition_year": None,
                "checkin_completed": checkin_done,
                "predicted_repeat_probability": score,
            }
            order_facts.append(order_fact)

        # ── Bulk insert order facts ───────────────────────────────────────────
        created_facts = AnalyticsOrderFact.objects.bulk_create(
            [AnalyticsOrderFact(event=event, **f) for f in order_facts],
            batch_size=200,
        )
        self.stdout.write(f"  Inserted {len(created_facts)} AnalyticsOrderFact rows.")

        # ── Bulk insert identities ────────────────────────────────────────────
        from ...models import AnalyticsIdentity
        identity_rows = []
        for fact in created_facts:
            if fact.repeat_hash:
                identity_rows.append(
                    AnalyticsIdentity(
                        order_fact=fact,
                        event=event,
                        identity_type="email",
                        identity_hash=fact.repeat_hash
                    )
                )
        if identity_rows:
            AnalyticsIdentity.objects.bulk_create(identity_rows, batch_size=500)
            self.stdout.write(f"  Inserted {len(identity_rows)} AnalyticsIdentity rows.")

        # ── Update repeat status from DB ──────────────────────────────────────
        # Now that rows are inserted, cross-edition repeat detection works.
        from ...services.repeat_detector import evaluate_repeat_status
        updated = 0
        for fact in created_facts:
            if not fact.repeat_hash:
                continue
            repeat_status = evaluate_repeat_status(
                event, [{"type": "email", "hash": fact.repeat_hash}]
            )
            if repeat_status["is_repeat_buyer"]:
                AnalyticsOrderFact.objects.filter(pk=fact.pk).update(**repeat_status)
                updated += 1
        self.stdout.write(f"  Marked {updated} orders as repeat buyers.")

        # ── Build ticket facts ────────────────────────────────────────────────
        rng2 = random.Random(f"tickets-{event.slug}-{edition_year}")
        for fact in created_facts:
            # Main ticket
            main_ticket = _weighted_choice(
                [(t, t["weight"]) for t in TICKET_TYPES], rng2
            )
            ticket_fact_rows.append(
                AnalyticsTicketFact(
                    order_fact=fact,
                    event=event,
                    item_id=TICKET_TYPES.index(main_ticket) + 1,
                    item_name=main_ticket["name"],
                    variation_id=None,
                    variation_name="",
                    price=main_ticket["price"],
                    tax_rate=Decimal("0.19"),
                    net_price=(main_ticket["price"] / Decimal("1.19")).quantize(Decimal("0.01")),
                    is_addon=False,
                    age_range=fact.age_range,
                    is_age_confirmed=fact.is_age_confirmed,
                    is_caravan_pass=False,
                    camper_van_length_bucket="",
                )
            )
            # Caravan pass ticket
            if fact.has_caravan_pass:
                ticket_fact_rows.append(
                    AnalyticsTicketFact(
                        order_fact=fact,
                        event=event,
                        item_id=len(TICKET_TYPES) + 1,
                        item_name=CARAVAN_TICKET["name"],
                        variation_id=None,
                        variation_name="",
                        price=CARAVAN_TICKET["price"],
                        tax_rate=Decimal("0.19"),
                        net_price=(CARAVAN_TICKET["price"] / Decimal("1.19")).quantize(Decimal("0.01")),
                        is_addon=True,
                        age_range="",
                        is_age_confirmed=None,
                        is_caravan_pass=True,
                        camper_van_length_bucket=fact.camper_van_length_bucket,
                    )
                )

        AnalyticsTicketFact.objects.bulk_create(ticket_fact_rows, batch_size=500)
        self.stdout.write(f"  Inserted {len(ticket_fact_rows)} AnalyticsTicketFact rows.")

        # ── Invalidate cohort cache ───────────────────────────────────────────
        from ...services.cohort_service import invalidate_cohort_cache
        invalidate_cohort_cache(series.slug, organizer.pk)

        self.stdout.write(
            self.style.SUCCESS(
                f"\n✓ Done. {n_orders} orders for {event.slug} ({edition_year}).\n"
                f"  Visit: /control/event/{organizer.slug}/{event.slug}/analytics/\n"
                f"\n  To generate all 4 editions for a full cohort matrix:\n"
                f"  python -m pretix generate_test_data --organizer {organizer.slug} "
                f"--event <event-2022> --edition-year 2022 --orders 80\n"
                f"  python -m pretix generate_test_data --organizer {organizer.slug} "
                f"--event <event-2023> --edition-year 2023 --orders 120\n"
                f"  python -m pretix generate_test_data --organizer {organizer.slug} "
                f"--event <event-2024> --edition-year 2024 --orders 160\n"
                f"  python -m pretix generate_test_data --organizer {organizer.slug} "
                f"--event <event-2026> --edition-year 2026 --orders 200"
            )
        )
