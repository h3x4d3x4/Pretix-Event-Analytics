"""
Exports.

Dashboard downloads (respect the current filter bar):
  orders CSV   one row per order fact
  tickets CSV  one row per ticket/add-on
  loyalty CSV  order codes with their returning status and edition history —
               lets organisers look people up in Pretix without the plugin
               ever storing names or addresses
  PDF          printable summary of every dashboard section

Pretix "Export" menu integration (CSV/Excel via ListExporter):
  Analytics: order facts / ticket facts
"""
import csv
import re
from collections import OrderedDict
from datetime import datetime

from django import forms
from django.dispatch import receiver
from django.http import HttpResponse, StreamingHttpResponse
from django.template.loader import render_to_string
from django.utils.translation import gettext as _, gettext_lazy

from pretix.base.exporter import ListExporter
from pretix.base.signals import register_data_exporters

from .models import AnalyticsOrderFact, AnalyticsTicketFact


def _safe_filename(slug: str, ext: str) -> str:
    """
    Sanitize a slug for use in Content-Disposition filenames: strictly
    [A-Za-z0-9_-]+, truncated to 50 chars, plus timestamp and extension.
    """
    if not isinstance(slug, str) or "\0" in slug or ".." in slug or "/" in slug or "\\" in slug:
        slug = "event"
    clean = re.sub(r"[^A-Za-z0-9_-]", "_", slug)[:50] or "event"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:8] or "bin"
    return f"analytics_{clean}_{timestamp}.{safe_ext}"


def _yn(v):
    return "" if v is None else ("yes" if v else "no")


def _dt(v, tz=None):
    if not v:
        return ""
    return (v.astimezone(tz) if tz else v).strftime("%Y-%m-%d %H:%M:%S")


# ── Row builders (shared by dashboard downloads and Pretix exporters) ─────────

ORDER_HEADER = [
    "event", "edition_year", "series", "order_code", "order_datetime", "payment_datetime", "days_before_event",
    "order_status", "total_gross", "total_net", "tax_amount", "fees_total", "refunded_amount", "canceled_at",
    "currency", "ticket_count", "addon_count", "is_group_order", "voucher_used", "payment_provider", "is_refunded",
    "country_code", "city", "postal_code", "is_local_buyer", "age_range", "is_age_confirmed", "language",
    "has_caravan_pass", "camper_van_length_bucket", "is_repeat_buyer", "repeat_from_last_edition", "repeat_count",
    "first_seen_edition_year", "editions_attended", "checkin_completed", "predicted_repeat_probability",
]


def order_rows(qs, tz=None):
    yield ORDER_HEADER
    for f in qs.select_related("event").order_by("event_id", "order_datetime").iterator(chunk_size=1000):
        yield [
            f.event.slug, f.edition_year or "", f.series_slug, f.order_code, _dt(f.order_datetime, tz),
            _dt(f.payment_datetime, tz), "" if f.days_before_event is None else f.days_before_event,
            f.get_order_status_display(), f.total_gross, f.total_net, f.tax_amount, f.fees_total, f.refunded_amount,
            _dt(f.canceled_at, tz), f.currency, f.ticket_count, f.addon_count, _yn(f.is_group_order),
            _yn(f.voucher_used), f.payment_provider, _yn(f.is_refunded), f.country_code, f.city, f.postal_code,
            _yn(f.is_local_buyer), f.age_range, _yn(f.is_age_confirmed), f.language, _yn(f.has_caravan_pass),
            f.camper_van_length_bucket, _yn(f.is_repeat_buyer), _yn(f.repeat_from_last_edition), f.repeat_count,
            f.first_seen_edition_year or "", f.editions_attended, _yn(f.checkin_completed),
            f.predicted_repeat_probability,
        ]


TICKET_HEADER = [
    "event", "order_code", "order_status", "product", "category", "variation", "is_addon", "price", "net_price",
    "tax_rate", "voucher_code", "voucher_tag", "age_range", "checked_in", "first_checkin_at",
    "attendee_identified", "attendee_is_buyer", "is_returning_attendee", "attendee_previous_editions",
    "attendee_first_seen_year",
]


def ticket_rows(qs, tz=None):
    yield TICKET_HEADER
    for t in qs.select_related("event", "order_fact").order_by("event_id", "order_fact_id", "id").iterator(chunk_size=1000):
        yield [
            t.event.slug, t.order_fact.order_code, t.order_fact.get_order_status_display(), t.item_name,
            t.item_category, t.variation_name, _yn(t.is_addon), t.price, t.net_price, t.tax_rate, t.voucher_code,
            t.voucher_tag, t.age_range, _yn(t.checked_in), _dt(t.first_checkin_at, tz), _yn(t.attendee_identified),
            _yn(t.attendee_is_buyer), _yn(t.is_returning_attendee), t.attendee_previous_editions,
            t.attendee_first_seen_year or "",
        ]


class _Echo:
    def write(self, value):
        return value


def _csv_response(rows, filename):
    writer = csv.writer(_Echo())
    response = StreamingHttpResponse((writer.writerow(r) for r in rows), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ── Dashboard downloads ───────────────────────────────────────────────────────

def _scope(request, event):
    from .services.reports.scope import ReportScope
    return ReportScope(request, event)


def export_orders_csv(request, event):
    scope = _scope(request, event)
    return _csv_response(order_rows(scope.orders, scope.tz), _safe_filename(event.slug, "csv"))


def export_tickets_csv(request, event):
    scope = _scope(request, event)
    return _csv_response(ticket_rows(scope.tickets, scope.tz), _safe_filename(f"{event.slug}_tickets", "csv"))


def export_loyalty_csv(request, event):
    """
    One row per order of this edition with the buyer's status and the list
    of series editions the buyer (as resolved person) took part in.
    """
    from .services.attendance import load_attendance, short_key

    scope = _scope(request, event)
    labels_by_person = {}
    if scope.series:
        att = load_attendance(scope.organizer_id, scope.series.slug, "people")
        years = [e.year for e in att.editions]
        for e in att.editions:
            label = str(e.year) if years.count(e.year) == 1 else f"{e.label} ({e.year})"
            for p in att.sets[e.key]:
                labels_by_person.setdefault(p, []).append(label)

    def rows():
        yield ["order_code", "buyer_status", "previous_editions", "first_seen", "last_edition_attended",
               "editions_attended", "tickets", "returning_attendees_in_order", "identified"]
        qs = scope.orders.filter(event=event).prefetch_related("ticket_facts").order_by("order_datetime")
        for f in qs.iterator(chunk_size=500):
            tickets = [t for t in f.ticket_facts.all() if not t.is_addon]
            hist = labels_by_person.get(short_key(f.person_key), []) if f.person_key else []
            yield [
                f.order_code,
                _("returning") if f.is_repeat_buyer else (_("first time") if f.person_key else _("unknown")),
                f.repeat_count, f.first_seen_edition_year or "",
                "yes" if f.repeat_from_last_edition else "no",
                " · ".join(hist), len(tickets), sum(1 for t in tickets if t.is_returning_attendee),
                "yes" if f.person_key else "no",
            ]
    return _csv_response(rows(), _safe_filename(f"{event.slug}_loyalty", "csv"))


def export_pdf(request, event) -> HttpResponse:
    """Printable report of every dashboard section (WeasyPrint, as used by Pretix)."""
    try:
        import weasyprint
    except ImportError:
        return HttpResponse("PDF export requires WeasyPrint. Install it with: pip install weasyprint",
                            status=500, content_type="text/plain")

    from .services.reports import audience, loyalty, operations, overview, sales, tickets

    scope = _scope(request, event)
    html = render_to_string("pretix_event_analytics/pdf_report.html", {
        "event": event, "config": scope.config, "scope": scope, "generated_at": datetime.now(scope.tz),
        "currency": scope.currency, "filter_chips": scope.filter_chips(),
        "overview": overview.build(scope), "sales": sales.build(scope), "audience": audience.build(scope),
        "loyalty": loyalty.build(scope), "tickets": tickets.build(scope), "operations": operations.build(scope),
    }, request=request)
    pdf_bytes = weasyprint.HTML(string=html).write_pdf()
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{_safe_filename(event.slug, "pdf")}"'
    return response


# ── Pretix Export menu ────────────────────────────────────────────────────────

class _AnalyticsListExporter(ListExporter):
    category = gettext_lazy("Analytics")
    repeatable_read = False

    @property
    def additional_form_fields(self):
        return OrderedDict([
            ("include_canceled", forms.BooleanField(
                label=gettext_lazy("Include canceled and refunded orders"), required=False)),
        ])

    def _orders(self, form_data):
        qs = AnalyticsOrderFact.objects.filter(event__in=self.events)
        if not form_data.get("include_canceled"):
            qs = qs.filter(order_status="p", is_refunded=False)
        return qs


class AnalyticsOrderExporter(_AnalyticsListExporter):
    identifier = "pretix_event_analytics_orders"
    verbose_name = gettext_lazy("Analytics: order facts")
    description = gettext_lazy("One row per order with pseudonymous analytics fields "
                               "(returning status, purchase timing, geography, age band). No names or e-mails.")

    def iterate_list(self, form_data):
        return order_rows(self._orders(form_data), self.timezone)

    def get_filename(self):
        return f"{self.events.first().organizer.slug}_analytics_orders"


class AnalyticsTicketExporter(_AnalyticsListExporter):
    identifier = "pretix_event_analytics_tickets"
    verbose_name = gettext_lazy("Analytics: ticket facts")
    description = gettext_lazy("One row per ticket and add-on with check-in, voucher and returning-attendee fields.")

    def iterate_list(self, form_data):
        return ticket_rows(AnalyticsTicketFact.objects.filter(order_fact__in=self._orders(form_data)), self.timezone)

    def get_filename(self):
        return f"{self.events.first().organizer.slug}_analytics_tickets"


@receiver(register_data_exporters, dispatch_uid="pretix_analytics_exporter_orders")
def register_order_exporter(sender, **kwargs):
    return AnalyticsOrderExporter


@receiver(register_data_exporters, dispatch_uid="pretix_analytics_exporter_tickets")
def register_ticket_exporter(sender, **kwargs):
    return AnalyticsTicketExporter
