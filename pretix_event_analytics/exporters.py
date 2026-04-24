"""
CSV and PDF exporters for the analytics dashboard.

CSV export: single file with all AnalyticsOrderFact rows for the event,
            with filters applied (same filter form as the dashboard).

PDF export: rendered via WeasyPrint (Pretix's own PDF engine) from a
            dedicated HTML template. Includes all dashboard sections
            as a printable report.
"""
import csv
import logging
import re
from datetime import datetime

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from .filters import apply_dashboard_filters
from .forms import DashboardFilterForm
from .models import AnalyticsOrderFact, EventAnalyticsConfig


def _safe_filename(slug: str, ext: str) -> str:
    """
    Sanitize a slug for use in Content-Disposition filenames.

    Rejects null bytes, quotes, path separators and traversal sequences —
    these can truncate the header on some clients or be interpreted as
    filesystem paths by naïve download handlers. The result is strictly
    [A-Za-z0-9_-]+ truncated to 50 chars, prefixed/suffixed with a fixed
    timestamp and extension.
    """
    if not isinstance(slug, str) or "\0" in slug or ".." in slug or "/" in slug or "\\" in slug:
        slug = "event"
    clean = re.sub(r"[^A-Za-z0-9_-]", "_", slug)[:50] or "event"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:8] or "bin"
    return f"analytics_{clean}_{timestamp}.{safe_ext}"

logger = logging.getLogger(__name__)


def _get_filtered_qs(request, event):
    """Return a filtered queryset based on request GET params."""
    filter_form = DashboardFilterForm(request.GET or None, event=event)
    filter_data = filter_form.cleaned_data if filter_form.is_valid() else {}
    qs = AnalyticsOrderFact.objects.filter(event=event, order_status="p")
    return apply_dashboard_filters(qs, filter_data)


def export_csv(request, event) -> HttpResponse:
    """
    Export all (filtered) AnalyticsOrderFact rows as CSV.
    No PII: repeat_hash is the only buyer identifier; no emails, names or IDs.
    """
    qs = _get_filtered_qs(request, event)

    filename = _safe_filename(event.slug, "csv")

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)

    # Header
    writer.writerow(
        [
            "order_code",
            "order_datetime",
            "payment_datetime",
            "order_status",
            "total_gross",
            "total_net",
            "tax_amount",
            "currency",
            "ticket_count",
            "unique_attendee_count",
            "is_group_order",
            "payment_provider",
            "is_refunded",
            "country_code",
            "city",
            "postal_code",
            "age_range",
            "is_age_confirmed",
            "language",
            "has_caravan_pass",
            "camper_van_length_bucket",
            "is_repeat_buyer",
            "repeat_from_last_edition",
            "repeat_from_any_previous",
            "repeat_count",
            "first_seen_edition_year",
            "checkin_completed",
            "predicted_repeat_probability",
            "edition_year",
            "series_slug",
        ]
    )

    # Data rows — iterate in chunks to avoid loading all into memory
    for fact in qs.iterator(chunk_size=500):
        writer.writerow(
            [
                fact.order_code,
                fact.order_datetime.strftime("%Y-%m-%d %H:%M:%S") if fact.order_datetime else "",
                fact.payment_datetime.strftime("%Y-%m-%d %H:%M:%S") if fact.payment_datetime else "",
                fact.order_status,
                fact.total_gross,
                fact.total_net,
                fact.tax_amount,
                fact.currency,
                fact.ticket_count,
                fact.unique_attendee_count,
                "yes" if fact.is_group_order else "no",
                fact.payment_provider,
                "yes" if fact.is_refunded else "no",
                fact.country_code,
                fact.city,
                fact.postal_code,
                fact.age_range,
                fact.is_age_confirmed,
                fact.language,
                "yes" if fact.has_caravan_pass else "no",
                fact.camper_van_length_bucket,
                "yes" if fact.is_repeat_buyer else "no",
                "yes" if fact.repeat_from_last_edition else "no",
                "yes" if fact.repeat_from_any_previous else "no",
                fact.repeat_count,
                fact.first_seen_edition_year or "",
                "yes" if fact.checkin_completed else "no",
                fact.predicted_repeat_probability,
                fact.edition_year or "",
                fact.series_slug,
            ]
        )

    return response


def export_pdf(request, event) -> HttpResponse:
    """
    Export a PDF analytics report using WeasyPrint.
    Renders the same aggregated data as the dashboard into a print-optimised template.
    """
    try:
        import weasyprint
    except ImportError:
        return HttpResponse(
            "PDF export requires WeasyPrint. Install it with: pip install weasyprint",
            status=500,
            content_type="text/plain",
        )

    from django.db.models import Avg, Count, Q, Sum

    from .services.cohort_service import build_cohort_matrix, get_cohort_sizes

    try:
        config = EventAnalyticsConfig.objects.select_related("series").get(event=event)
    except EventAnalyticsConfig.DoesNotExist:
        config = None

    qs = _get_filtered_qs(request, event)

    agg = qs.aggregate(
        total_revenue=Sum("total_gross"),
        total_orders=Count("id"),
        total_tickets=Sum("ticket_count"),
        avg_order_value=Avg("total_gross"),
        repeat_count=Count("id", filter=Q(is_repeat_buyer=True)),
    )

    total_orders = agg["total_orders"] or 0
    repeat_count = agg["repeat_count"] or 0
    repeat_pct = round(repeat_count / total_orders * 100, 1) if total_orders else 0

    country_breakdown = list(
        qs.exclude(country_code="")
        .values("country_code")
        .annotate(count=Count("id"))
        .order_by("-count")[:20]
    )
    age_breakdown = list(
        qs.exclude(age_range="")
        .values("age_range")
        .annotate(count=Count("id"))
        .order_by("age_range")
    )

    cohort_matrix = {}
    cohort_sizes = {}
    cohort_years = []
    if config and config.series:
        cohort_matrix = build_cohort_matrix(config.series.slug, event.organizer_id)
        cohort_sizes = get_cohort_sizes(config.series.slug, event.organizer_id)
        cohort_years = sorted(cohort_matrix.keys())

    html_string = render_to_string(
        "pretix_event_analytics/pdf_report.html",
        {
            "event": event,
            "config": config,
            "generated_at": datetime.now(),
            "total_revenue": agg["total_revenue"] or 0,
            "total_orders": total_orders,
            "total_tickets": agg["total_tickets"] or 0,
            "avg_order_value": agg["avg_order_value"] or 0,
            "repeat_count": repeat_count,
            "repeat_pct": repeat_pct,
            "country_breakdown": country_breakdown,
            "age_breakdown": age_breakdown,
            "cohort_matrix": cohort_matrix,
            "cohort_sizes": cohort_sizes,
            "cohort_years": cohort_years,
            "all_cohort_years": sorted(cohort_sizes.keys()),
        },
        request=request,
    )

    pdf_bytes = weasyprint.HTML(string=html_string).write_pdf()

    filename = _safe_filename(event.slug, "pdf")

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write(pdf_bytes)
    return response
