#!/usr/bin/env python
"""
DEVELOPMENT ONLY — seed a local Pretix instance with realistic *real* orders.

Unlike ``generate_test_data`` (which writes fake analytics rows), this script
creates genuine Pretix orders, positions, answers, payments, refunds,
vouchers and check-ins for a series of yearly editions, so the whole
ingestion pipeline (resync → identity resolution → dashboards) can be
exercised end to end.

It writes to Pretix core tables, so it lives outside the plugin package, is
never shipped, and refuses to run unless the database is a local SQLite file
and ``--i-understand-this-writes-orders`` is passed.

    PRETIX_CONFIG_FILE=pretix.cfg .venv/bin/python scripts/dev_seed_orders.py \\
        --organizer suti --i-understand-this-writes-orders
"""
import argparse
import datetime as dt
import os
import random
import sys
from decimal import Decimal

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pretix.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.db import transaction  # noqa: E402
from django_scopes import scopes_disabled  # noqa: E402

EDITIONS = [  # year, event date, people, sales open
    (2022, dt.datetime(2022, 8, 25, 16, tzinfo=dt.timezone.utc), 330, dt.date(2021, 12, 1)),
    (2023, dt.datetime(2023, 8, 24, 16, tzinfo=dt.timezone.utc), 400, dt.date(2022, 12, 1)),
    (2024, dt.datetime(2024, 8, 29, 16, tzinfo=dt.timezone.utc), 470, dt.date(2023, 12, 1)),
    (2025, dt.datetime(2025, 8, 28, 16, tzinfo=dt.timezone.utc), 520, dt.date(2024, 12, 1)),
    (2026, dt.datetime(2026, 8, 27, 16, tzinfo=dt.timezone.utc), 610, dt.date(2025, 12, 28)),
    (2027, dt.datetime(2027, 8, 26, 16, tzinfo=dt.timezone.utc), 620, dt.date(2026, 8, 20)),  # on sale now
]
COUNTRIES = [("PT", 46), ("ES", 12), ("GB", 9), ("DE", 8), ("FR", 7), ("NL", 5), ("IT", 4), ("BE", 3),
             ("IE", 2), ("US", 2), ("BR", 2)]
FIRST = ["Ana", "João", "Maria", "Pedro", "Inês", "Tiago", "Sofia", "Rui", "Marta", "Luís", "Clara", "Hugo",
         "Emma", "Liam", "Lena", "Jonas", "Chloé", "Lucas", "Mia", "Noah", "Sara", "Diego", "Olivia", "Tom"]
LAST = ["Silva", "Santos", "Ferreira", "Costa", "Oliveira", "Martins", "Sousa", "Pereira", "García", "Smith",
        "Müller", "Dubois", "de Vries", "Rossi", "Murphy", "Lopes", "Almeida", "Ribeiro", "Carvalho", "Gomes"]
HEARD = ["Friends", "Instagram", "Been before", "Newspaper", "Other festival"]


def weighted(rng, pool):
    return rng.choices([v for v, _w in pool], weights=[w for _v, w in pool])[0]


class Person:
    def __init__(self, i, rng):
        self.email = f"person{i}@example.org"
        self.name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        self.birth = dt.date(rng.randint(1958, 2006), rng.randint(1, 12), rng.randint(1, 28))
        self.country = weighted(rng, COUNTRIES)
        self.provider = rng.choices(["stripe", "paypal", "banktransfer"], weights=[60, 25, 15])[0]
        self.card = f"fp_{i:06d}"
        self.loyalty = rng.random()  # propensity to come back


def order_time(rng, open_date, event_dt, now):
    """Opening rush, a quiet middle, and a build-up in the last six weeks."""
    window = (event_dt.date() - open_date).days
    r = rng.random()
    if r < 0.14:
        days = rng.randint(0, 3)
    elif r < 0.55:
        days = rng.randint(4, max(5, window - 45))
    elif r < 0.9:
        days = window - rng.randint(8, 45)
    else:
        days = window - rng.randint(0, 7)
    t = dt.datetime.combine(open_date + dt.timedelta(days=max(0, min(days, window))),
                            dt.time(rng.choices(range(24), weights=[1, 1, 1, 1, 1, 1, 2, 3, 4, 6, 7, 8, 9, 8, 8, 8, 8,
                                                                     9, 10, 11, 12, 10, 6, 3])[0], rng.randint(0, 59)),
                            tzinfo=dt.timezone.utc)
    return t if t < now else None


def ensure_event(org, series, year, date_from, open_date):
    from pretix.base.models import Event, Question
    from pretix_event_analytics.models import EventAnalyticsConfig

    slug = f"suti-festival-{year}"
    event = Event.objects.filter(organizer=org, slug=slug).first()
    if event is None:
        event = Event.objects.create(organizer=org, slug=slug, name=f"Suti Festival {year}", currency="EUR",
                                     date_from=date_from, plugins="pretix_event_analytics")
    event.date_from = date_from
    event.date_to = date_from + dt.timedelta(days=4)
    if "pretix_event_analytics" not in (event.plugins or ""):
        event.plugins = ",".join(p for p in [event.plugins, "pretix_event_analytics"] if p)
    event.presale_start = dt.datetime.combine(open_date, dt.time(10), tzinfo=dt.timezone.utc)
    event.save()
    event.settings.timezone = "Europe/Lisbon"
    event.settings.locale = "en"
    EventAnalyticsConfig.objects.update_or_create(
        event=event, defaults={"series": series, "edition_year": year, "home_country": "PT", "is_active": True},
    )

    if not event.items.exists():
        cat = event.categories.create(name="Festival passes")
        addcat = event.categories.create(name="Extras", is_addon=True)
        items = {
            "pass": event.items.create(name="Festival Pass", default_price=Decimal("89.00"), category=cat, admission=True),
            "vip": event.items.create(name="VIP Pass", default_price=Decimal("180.00"), category=cat, admission=True),
            "day": event.items.create(name="Day Pass", default_price=Decimal("45.00"), category=cat, admission=True),
            "van": event.items.create(name="Camper Van Pass", default_price=Decimal("60.00"), category=addcat),
            "parking": event.items.create(name="Parking", default_price=Decimal("15.00"), category=addcat),
        }
        items["day"].variations.create(value="Friday", default_price=Decimal("45.00"))
        items["day"].variations.create(value="Saturday", default_price=Decimal("49.00"))
        for key in ("pass", "vip", "day"):
            items[key].addons.create(addon_category=addcat, min_count=0, max_count=2)
        birth = event.questions.create(question="Birth Date", type=Question.TYPE_DATE, required=False)
        heard = event.questions.create(question="How did you hear about us?", type=Question.TYPE_CHOICE, required=False)
        for h in HEARD:
            heard.options.create(answer=h)
        van_q = event.questions.create(question="How long is your camper van (m)?", type=Question.TYPE_NUMBER,
                                       required=False)
        for it in (items["pass"], items["vip"], items["day"]):
            birth.items.add(it)
            heard.items.add(it)
        van_q.items.add(items["van"])
        event.checkin_lists.create(name="Gate")
        cfg = event.analytics_config
        cfg.tracked_question_ids = [heard.pk]
        cfg.ticket_target = 700 if year == 2027 else None
        cfg.save()
    return event


def seed_edition(event, people, attended_before, n_people, rng, now, open_date):
    from pretix.base.models import (
        Checkin, InvoiceAddress, Order, OrderPayment, OrderRefund, Voucher,
    )

    items = {str(i.name): i for i in event.items.all()}
    fp, vip, day, van, parking = (items["Festival Pass"], items["VIP Pass"], items["Day Pass"],
                                  items["Camper Van Pass"], items["Parking"])
    variations = list(day.variations.all())
    questions = {str(q.question): q for q in event.questions.all()}
    birth_q = questions["Birth Date"]
    heard_q = questions["How did you hear about us?"]
    heard_opts = list(heard_q.options.all())
    van_q = questions["How long is your camper van (m)?"]
    clist = event.checkin_lists.first()
    channel = event.organizer.sales_channels.get(identifier="web")
    crew = [Voucher.objects.create(event=event, code=f"CREW{event.pk}{i:02d}", tag="crew", item=fp,
                                   price_mode="set", value=Decimal("0.00")) for i in range(12)]
    promo = Voucher.objects.create(event=event, code=f"FRIENDS{event.pk}", tag="friends-promo", max_usages=200,
                                   price_mode="percent", value=Decimal("10.00"))

    # Who comes this year: returning people by loyalty, then fresh faces.
    returning = [p for p in attended_before if rng.random() < 0.18 + 0.5 * p.loyalty]
    fresh_pool = [p for p in people if p not in attended_before]
    rng.shuffle(fresh_pool)
    attendees = returning + fresh_pool[: max(0, n_people - len(returning))]
    rng.shuffle(attendees)

    window_end = event.date_from
    created, sold = 0, []
    i = 0
    code_n = 0
    while i < len(attendees):
        buyer = attendees[i]
        size = rng.choices([1, 2, 3, 4, 5], weights=[58, 27, 8, 5, 2])[0]
        group = attendees[i:i + size]
        i += size
        when = order_time(rng, open_date, window_end, now)
        if when is None:
            continue
        early = (when.date() - open_date).days < 40
        late = (window_end.date() - when.date()).days < 21
        code_n += 1
        order = Order.objects.create(
            code=f"{event.slug[-4:]}{code_n:04d}".upper().replace("-", ""), event=event, email=buyer.email,
            status=Order.STATUS_PAID, locale=rng.choice(["en", "en", "pt", "pt", "de", "es"]),
            datetime=when, expires=when + dt.timedelta(days=7), total=Decimal("0"), sales_channel=channel,
        )
        InvoiceAddress.objects.create(order=order, country=buyer.country, city="", name_cached=buyer.name)
        total = Decimal("0")
        use_voucher = crew.pop() if crew and rng.random() < 0.03 else (promo if rng.random() < 0.05 else None)
        for j, person in enumerate(group):
            kind = rng.choices(["pass", "vip", "day"], weights=[70, 12, 18])[0]
            item = {"pass": fp, "vip": vip, "day": day}[kind]
            variation = rng.choice(variations) if kind == "day" else None
            base = variation.default_price if variation else item.default_price
            # Price tiers: early bird −20%, last three weeks +15%
            price = (base * Decimal("0.8") if early else base * Decimal("1.15") if late else base).quantize(Decimal("1.00"))
            voucher = None
            if use_voucher and j == 0:
                voucher = use_voucher
                price = Decimal("0.00") if voucher.tag == "crew" else (price * Decimal("0.9")).quantize(Decimal("1.00"))
            share_email = j == 0 or rng.random() < 0.6
            pos = order.all_positions.create(
                item=item, variation=variation, price=price, voucher=voucher,
                attendee_name_parts={"full_name": person.name},
                attendee_email=person.email if share_email and j > 0 else None,
            )
            total += price
            if rng.random() < 0.85:
                pos.answers.create(question=birth_q, answer=person.birth.isoformat())
            if rng.random() < 0.7:
                opt = rng.choice(heard_opts if person not in attended_before else heard_opts[:3] + [heard_opts[2]] * 3)
                a = pos.answers.create(question=heard_q, answer=str(opt.answer))
                a.options.add(opt)
            if j == 0 and rng.random() < 0.12:
                addon = order.all_positions.create(item=van, price=van.default_price, addon_to=pos)
                addon.answers.create(question=van_q, answer=str(rng.choice([5.4, 6.2, 7.1, 7.8, 8.6, 9.3])))
                total += van.default_price
            if rng.random() < 0.18:
                order.all_positions.create(item=parking, price=parking.default_price, addon_to=pos)
                total += parking.default_price
            sold.append((pos, person))
        order.total = total
        order.save()
        info = {}
        if buyer.provider == "stripe":
            info = {"payment_method_details": {"card": {"fingerprint": buyer.card, "country": buyer.country}}}
        elif buyer.provider == "paypal":
            info = {"payer": {"payer_info": {"payer_id": f"PP{buyer.card}", "country_code": buyer.country}}}
        elif buyer.provider == "banktransfer":
            info = {"iban": f"{buyer.country}50000000{buyer.card[-6:]}"}
        pay = order.payments.create(provider=buyer.provider, amount=total, state=OrderPayment.PAYMENT_STATE_CONFIRMED,
                                    payment_date=when + dt.timedelta(minutes=rng.randint(1, 90)
                                                                     if buyer.provider != "banktransfer" else 60 * 24 * rng.randint(1, 4)))
        pay.info_data = info
        pay.save()
        if rng.random() < 0.03:
            order.status = Order.STATUS_CANCELED
            order.cancellation_date = when + dt.timedelta(days=rng.randint(3, 60))
            order.save()
            order.all_positions.update(canceled=True)
            order.refunds.create(provider=buyer.provider, amount=total, state=OrderRefund.REFUND_STATE_DONE,
                                 source=OrderRefund.REFUND_SOURCE_ADMIN, execution_date=order.cancellation_date)
            sold = [(p, x) for p, x in sold if p.order_id != order.pk]
        created += 1

    if event.date_from < now:
        for pos, _person in sold:
            if rng.random() < 0.91:
                Checkin.objects.create(position=pos, list=clist, datetime=event.date_from + dt.timedelta(
                    hours=rng.choices([0, 1, 2, 3, 5, 8, 20, 26, 44], weights=[10, 22, 18, 12, 8, 6, 12, 7, 5])[0],
                    minutes=rng.randint(0, 59)))
    return created, {x for _p, x in sold}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--organizer", required=True)
    ap.add_argument("--series", default="suti-festival")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--i-understand-this-writes-orders", action="store_true", dest="ok")
    args = ap.parse_args()

    db = settings.DATABASES["default"]
    if not args.ok or "sqlite" not in db["ENGINE"]:
        sys.exit("Refusing: pass --i-understand-this-writes-orders and use a local SQLite dev database.")

    from pretix.base.models import Order, Organizer
    from pretix_event_analytics.models import EventSeries

    rng = random.Random(args.seed)
    now = dt.datetime(2026, 9, 25, 9, tzinfo=dt.timezone.utc)
    with scopes_disabled(), transaction.atomic():
        org = Organizer.objects.get(slug=args.organizer)
        series, _ = EventSeries.objects.get_or_create(organizer=org, slug=args.series,
                                                      defaults={"name": "Suti Festival"})
        people = [Person(i, rng) for i in range(2600)]
        attended_before = set()
        for year, date_from, n, open_date in EDITIONS:
            event = ensure_event(org, series, year, date_from, open_date)
            if Order.objects.filter(event=event).exists():
                print(f"{event.slug}: already has orders, skipping")
                continue
            created, came = seed_edition(event, people, attended_before, n, rng, now, open_date)
            attended_before |= came
            print(f"{event.slug}: {created} orders")
    print("Done. Now run: python -m pretix analytics_resync --organizer", args.organizer, "--all")


if __name__ == "__main__":
    main()
