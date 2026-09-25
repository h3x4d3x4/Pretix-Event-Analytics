"""
ReportScope — what a dashboard page is looking at.

Built once per request from the filter bar. Every section builder receives
the scope and reads facts only through it, so filters, edition merging,
timezone handling and caching behave identically on every page.
"""
import datetime
import hashlib
from functools import cached_property
from typing import List, Optional
from zoneinfo import ZoneInfo

from django.db.models import Q, QuerySet

from ...forms import DashboardFilterForm
from ...models import AnalyticsOrderFact, AnalyticsTicketFact, EventAnalyticsConfig
from ..versioning import cached


def pct(part, whole, digits=1) -> float:
    return round(part / whole * 100, digits) if whole else 0.0


class ReportScope:
    def __init__(self, request, event=None, params=None):
        self.request = request
        self.event = event or request.event
        self.organizer_id = self.event.organizer_id
        self.params = params if params is not None else request.GET
        self.config: Optional[EventAnalyticsConfig] = (
            EventAnalyticsConfig.objects.select_related("series").filter(event=self.event).first()
        )
        self.form = DashboardFilterForm(self.params or None, event=self.event)
        self.filters = self.form.cleaned_data if self.form.is_valid() else {}
        edition_ids = [int(e) for e in self.filters.get("editions") or []]
        self.event_ids: List[int] = edition_ids or [self.event.pk]
        self.merged = len(self.event_ids) > 1 or self.event_ids != [self.event.pk]
        self.tz = ZoneInfo(str(self.event.settings.timezone))
        self.currency = self.event.currency

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def series(self):
        return self.config.series if self.config else None

    @cached_property
    def cache_key(self) -> str:
        items = sorted((k, tuple(sorted(self.params.getlist(k)))) for k in self.params.keys()) if hasattr(
            self.params, "getlist") else sorted(self.params.items())
        raw = f"{self.event.pk}|{items}"
        return hashlib.sha1(raw.encode()).hexdigest()

    def cached(self, section: str, fn):
        return cached(self.organizer_id, f"report:{section}:{self.cache_key}", fn)

    # ── Querysets ─────────────────────────────────────────────────────────────

    def filter_orders(self, qs: QuerySet, *, status: bool = True) -> QuerySet:
        """Apply the filter bar to an AnalyticsOrderFact queryset."""
        f = self.filters
        if status:
            if f.get("include_refunded"):
                qs = qs.filter(order_status__in=("p", "c"))
            else:
                qs = qs.filter(order_status="p", is_refunded=False)
        if f.get("date_from"):
            qs = qs.filter(order_datetime__gte=datetime.datetime.combine(f["date_from"], datetime.time.min, self.tz))
        if f.get("date_to"):
            qs = qs.filter(order_datetime__lt=datetime.datetime.combine(
                f["date_to"] + datetime.timedelta(days=1), datetime.time.min, self.tz))
        if f.get("country"):
            qs = qs.filter(country_code=f["country"].upper())
        if f.get("age_range"):
            qs = qs.filter(age_range=f["age_range"])
        if f.get("buyer_type") == "new":
            qs = qs.filter(is_repeat_buyer=False)
        elif f.get("buyer_type") == "returning":
            qs = qs.filter(is_repeat_buyer=True)
        if f.get("provider"):
            qs = qs.filter(payment_provider=f["provider"])
        if f.get("has_caravan"):
            qs = qs.filter(has_caravan_pass=True)
        if f.get("ticket_type"):
            qs = qs.filter(pk__in=AnalyticsTicketFact.objects.filter(
                item_name__in=f["ticket_type"]).values("order_fact_id"))
        return qs

    def orders_for(self, event_ids, *, status: bool = True) -> QuerySet:
        return self.filter_orders(AnalyticsOrderFact.objects.filter(event_id__in=list(event_ids)), status=status)

    @cached_property
    def orders(self) -> QuerySet:
        """Filtered order facts in scope (paid, not refunded unless asked)."""
        return self.orders_for(self.event_ids)

    @cached_property
    def orders_any_status(self) -> QuerySet:
        """Filtered order facts regardless of status — for refund/cancel stats."""
        return self.orders_for(self.event_ids, status=False)

    @cached_property
    def tickets(self) -> QuerySet:
        """All ticket facts (admissions and add-ons) of the filtered orders."""
        return AnalyticsTicketFact.objects.filter(order_fact__in=self.orders)

    @cached_property
    def admissions(self) -> QuerySet:
        return self.tickets.filter(is_addon=False)

    # ── Editions (for comparisons) ────────────────────────────────────────────

    @cached_property
    def series_events(self) -> list:
        """[(event, config)] for every active Pretix edition of the series, oldest first."""
        if not self.series:
            return [(self.event, self.config)]
        configs = (
            EventAnalyticsConfig.objects.filter(series=self.series)
            .select_related("event")
            .order_by("edition_year", "event__date_from", "event_id")
        )
        return [(c.event, c) for c in configs if c.is_active or c.event_id == self.event.pk]

    @cached_property
    def previous_edition(self):
        """The active edition right before this one, or None."""
        prev = None
        for ev, _cfg in self.series_events:
            if ev.pk == self.event.pk:
                return prev
            prev = ev
        return None

    # ── Time helpers ──────────────────────────────────────────────────────────

    def local(self, dt):
        return dt.astimezone(self.tz) if dt else None

    def local_date(self, dt):
        return dt.astimezone(self.tz).date() if dt else None

    @cached_property
    def event_start(self):
        return self.event.date_from

    @cached_property
    def is_upcoming(self) -> bool:
        from django.utils.timezone import now
        return bool(self.event.date_from and self.event.date_from > now())

    def days_until_event(self) -> Optional[int]:
        from django.utils.timezone import now
        if not self.event.date_from:
            return None
        return (self.local_date(self.event.date_from) - self.local_date(now())).days

    # ── Filter chips ──────────────────────────────────────────────────────────

    def filter_chips(self) -> List[str]:
        from django.utils.translation import gettext as _

        from ...forms import provider_label

        f = self.filters
        chips = []
        if f.get("date_from"):
            chips.append(f"{_('From')}: {f['date_from']:%Y-%m-%d}")
        if f.get("date_to"):
            chips.append(f"{_('Until')}: {f['date_to']:%Y-%m-%d}")
        if f.get("country"):
            chips.append(f"{_('Country')}: {f['country']}")
        if f.get("age_range"):
            chips.append(f"{_('Age')}: {f['age_range']}")
        if f.get("buyer_type") == "new":
            chips.append(_("First-time buyers"))
        elif f.get("buyer_type") == "returning":
            chips.append(_("Returning buyers"))
        if f.get("provider"):
            chips.append(f"{_('Payment')}: {provider_label(f['provider'])}")
        if f.get("ticket_type"):
            chips.append(f"{_('Products')}: {len(f['ticket_type'])}")
        if f.get("editions"):
            chips.append(f"{_('Events')}: {len(f['editions'])}")
        if f.get("include_refunded"):
            chips.append(_("Incl. canceled & refunded"))
        if f.get("has_caravan"):
            chips.append(_("Caravan only"))
        return chips

    @property
    def is_filtered(self) -> bool:
        return bool(self.filter_chips())


def edition_status_q() -> Q:
    """Orders that count as 'sold' — paid and not fully refunded."""
    return Q(order_status="p", is_refunded=False)
