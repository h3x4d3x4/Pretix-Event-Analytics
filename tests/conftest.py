"""
Shared pytest fixtures.

Tests build *real* Pretix objects (Organizer, Event, Item, Order,
OrderPosition, OrderPayment, Checkin) and push them through the plugin's
ingestion pipeline, so they exercise the same code paths as production.
Fixture code lives outside the plugin package and is never shipped.
"""
import datetime
import itertools
from decimal import Decimal

import pytest
from django.utils import timezone
from django_scopes import scopes_disabled

_code_counter = itertools.count(1)


@pytest.fixture(autouse=True)
def _analytics_settings(settings):
    settings.PRETIX_ANALYTICS_SECRET_SALT = "test-salt-0123456789abcdef"


@pytest.fixture(autouse=True)
def _real_cache(settings):
    # Pretix's test settings use DummyCache; debounce keys, resync locks and
    # report caching need a cache that actually stores values.
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                                   "LOCATION": "analytics-tests"}}


@pytest.fixture(autouse=True)
def _clear_cache(_real_cache):
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def organizer(db):
    from pretix.base.models import Organizer
    with scopes_disabled():
        return Organizer.objects.create(name="Suti", slug="suti")


@pytest.fixture
def series(organizer):
    from pretix_event_analytics.models import EventSeries
    return EventSeries.objects.create(organizer=organizer, name="Suti Festival", slug="suti-festival")


class EventKit:
    """An event plus its products and a small order factory."""

    def __init__(self, event):
        from pretix.base.models import Question
        self.event = event
        with scopes_disabled():
            self.ga = event.items.create(name="General Admission", default_price=Decimal("100.00"), admission=True)
            self.vip = event.items.create(name="VIP", default_price=Decimal("200.00"), admission=True)
            self.parking = event.items.create(name="Parking", default_price=Decimal("20.00"), admission=False)
            self.birth_q = event.questions.create(question="Birth Date", type=Question.TYPE_DATE, required=False)
            self.diet_q = event.questions.create(question="Diet", type=Question.TYPE_CHOICE, required=False)
            self.diet_veg = self.diet_q.options.create(answer="Vegetarian")
            self.diet_any = self.diet_q.options.create(answer="Anything")
            self.checkin_list = event.checkin_lists.create(name="Main")

    def order(self, email, tickets=None, *, status="p", days_before=60, provider="banktransfer",
              country="PT", fees=Decimal("0.00"), refund=False, code=None, payment_info=None):
        """
        Create an order. ``tickets`` is a list of dicts:
            {"item": Item, "attendee_email": str|None, "attendee_name": str|None,
             "birth": "YYYY-MM-DD"|None, "diet": QuestionOption|None,
             "addons": [Item, ...], "price": Decimal|None, "voucher": Voucher|None}
        """
        from pretix.base.models import InvoiceAddress, Order, OrderFee, OrderPayment, OrderRefund
        tickets = tickets if tickets is not None else [{"item": self.ga}]
        event = self.event
        dt = event.date_from - datetime.timedelta(days=days_before)
        total = Decimal("0.00")
        with scopes_disabled():
            order = Order.objects.create(
                code=code or f"T{next(_code_counter):04d}",
                event=event, email=email, status=status, locale="en",
                datetime=dt, expires=dt + datetime.timedelta(days=10), total=Decimal("0.00"),
                sales_channel=event.organizer.sales_channels.get(identifier="web"),
            )
            InvoiceAddress.objects.create(order=order, country=country)
            for t in tickets:
                price = t.get("price", t["item"].default_price)
                parent = order.all_positions.create(
                    item=t["item"], price=price, attendee_email=t.get("attendee_email"),
                    attendee_name_parts={"full_name": t["attendee_name"]} if t.get("attendee_name") else {},
                    voucher=t.get("voucher"), canceled=(status == "c"),
                )
                total += price
                if t.get("birth"):
                    parent.answers.create(question=self.birth_q, answer=t["birth"])
                if t.get("diet"):
                    a = parent.answers.create(question=self.diet_q, answer=str(t["diet"].answer))
                    a.options.add(t["diet"])
                for addon in t.get("addons", []):
                    order.all_positions.create(item=addon, price=addon.default_price, addon_to=parent,
                                               canceled=(status == "c"))
                    total += addon.default_price
            if fees:
                OrderFee.objects.create(order=order, fee_type=OrderFee.FEE_TYPE_PAYMENT, value=fees,
                                        tax_value=Decimal("0.00"), canceled=(status == "c"))
                total += fees
            order.total = total
            if status == "c":
                order.cancellation_date = dt + datetime.timedelta(days=5)
            order.save()
            if status in ("p", "c"):
                p = order.payments.create(provider=provider, amount=total, state=OrderPayment.PAYMENT_STATE_CONFIRMED,
                                          payment_date=dt + datetime.timedelta(hours=1))
                if payment_info:
                    p.info_data = payment_info
                    p.save()
            if refund:
                order.refunds.create(provider=provider, amount=total, state=OrderRefund.REFUND_STATE_DONE,
                                     source=OrderRefund.REFUND_SOURCE_ADMIN, execution_date=dt + datetime.timedelta(days=5))
        return order

    def checkin(self, order):
        from pretix.base.models import Checkin
        from pretix.base.signals import checkin_created
        with scopes_disabled():
            for pos in order.positions.filter(addon_to__isnull=True):
                ci = Checkin.objects.create(position=pos, list=self.checkin_list, datetime=self.event.date_from)
                # perform_checkin() sends this in production; model creation alone does not.
                checkin_created.send(self.event, checkin=ci)


@pytest.fixture
def make_edition(organizer, series):
    """make_edition(2024) → EventKit for a configured edition of the series."""
    from pretix.base.models import Event
    from pretix_event_analytics.models import EventAnalyticsConfig

    def _make(year, *, in_series=True, date=None):
        with scopes_disabled():
            event = Event.objects.create(
                organizer=organizer, name=f"Suti {year}", slug=f"suti-{year}",
                date_from=date or datetime.datetime(year, 8, 27, 18, 0, tzinfo=datetime.timezone.utc),
                plugins="pretix_event_analytics", currency="EUR",
            )
            event.settings.timezone = "Europe/Lisbon"
        EventAnalyticsConfig.objects.create(
            event=event, series=series if in_series else None, edition_year=year, home_country="PT",
        )
        return EventKit(event)

    return _make


@pytest.fixture
def ingest():
    """ingest(order) → runs the live order_paid pipeline synchronously."""
    from pretix_event_analytics.tasks import process_order_paid

    def _ingest(*orders):
        for o in orders:
            process_order_paid.apply(args=[o.pk]).get()
    return _ingest


@pytest.fixture
def admin_client(client, organizer):
    from pretix.base.models import User
    user = User.objects.create_user("admin@example.org", "admin")
    with scopes_disabled():
        team = organizer.teams.create(
            name="Admins", all_events=True, can_view_orders=True, can_change_event_settings=True,
            can_change_organizer_settings=True, can_change_items=True,
        )
        team.members.add(user)
    client.login(email="admin@example.org", password="admin")
    return client


def now():
    return timezone.now()
