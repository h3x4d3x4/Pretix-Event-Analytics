"""
Views for the pretix_event_analytics plugin.

URL structure:
  Event-level (EventPermissionRequiredMixin):
    /control/event/<org>/<event>/analytics/                → DashboardView
    /control/event/<org>/<event>/analytics/config/         → EventConfigView
    /control/event/<org>/<event>/analytics/export/csv/     → ExportCSVView
    /control/event/<org>/<event>/analytics/export/pdf/     → ExportPDFView

  Organizer-level (OrganizerPermissionRequiredMixin):
    /control/organizer/<org>/analytics/series/             → SeriesListView
    /control/organizer/<org>/analytics/series/create/      → SeriesCreateView
    /control/organizer/<org>/analytics/series/<pk>/edit/   → SeriesEditView
    /control/organizer/<org>/analytics/series/<pk>/delete/ → SeriesDeleteView
"""
import json
import logging
from collections import defaultdict
from decimal import Decimal

import pycountry
from django.contrib import messages
from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.db.models.functions import TruncDay, TruncHour
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DeleteView, FormView, ListView, TemplateView, UpdateView

from pretix.base.models import Event
from pretix.control.permissions import (
    EventPermissionRequiredMixin,
    OrganizerPermissionRequiredMixin,
)

from .filters import apply_dashboard_filters
from .forms import DashboardFilterForm, EventAnalyticsConfigForm, EventSeriesForm
from .models import AnalyticsOrderFact, AnalyticsTicketFact, EventAnalyticsConfig, EventSeries
from .services.cohort_service import build_cohort_matrix, get_cohort_sizes
from .services.secondary_market import get_name_change_stats, get_name_changes_by_edition

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _json_default(obj):
    """JSON encoder for Decimal and other non-serialisable types."""
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serialisable")


def _country_flag(code: str) -> str:
    """Convert a 2-letter ISO country code into a unicode flag emoji."""
    if not code or len(code) != 2:
        return code
    try:
        return "".join(chr(ord(c) + 127397) for c in code.upper())
    except Exception:
        return code


def _to_json(data) -> str:
    return json.dumps(data, default=_json_default)


# ── Dashboard ─────────────────────────────────────────────────────────────────

class DashboardView(EventPermissionRequiredMixin, TemplateView):
    """
    Main analytics dashboard.  All data served from AnalyticsOrderFact only.
    Redirects to config page if the event has not been configured yet.
    """
    permission = "can_view_orders"
    template_name = "pretix_event_analytics/dashboard.html"

    def get(self, request, *args, **kwargs):
        if not EventAnalyticsConfig.objects.filter(event=request.event).exists():
            messages.info(
                request,
                _("Please configure analytics for this event before viewing the dashboard."),
            )
            return redirect(
                reverse(
                    "plugins:pretix_event_analytics:config",
                    kwargs={
                        "organizer": request.organizer.slug,
                        "event": request.event.slug,
                    },
                )
            )
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        request = self.request
        event = request.event

        config = EventAnalyticsConfig.objects.select_related("series").get(event=event)

        # ── Empty state check ─────────────────────────────────────────────────
        has_data = AnalyticsOrderFact.objects.filter(event=event).exists()
        can_resync = request.user.has_event_permission(
            request.organizer, event, "can_change_event_settings", request
        )
        resync_url = reverse(
            "plugins:pretix_event_analytics:trigger_resync",
            kwargs={
                "organizer": request.organizer.slug,
                "event": request.event.slug,
            },
        )

        # ── Filter form ───────────────────────────────────────────────────────
        filter_form = DashboardFilterForm(request.GET or None, event=event)
        filter_data = filter_form.cleaned_data if filter_form.is_valid() else {}

        # Base queryset — handle multi-edition filtering
        editions = filter_data.get("editions", []) if filter_data else []
        if editions:
            # Security: ensure selected editions actually belong to this organizer
            valid_event_ids = [
                e.id for e in Event.objects.filter(
                    organizer=request.organizer, id__in=editions
                )
            ]
            base_qs = AnalyticsOrderFact.objects.filter(
                event_id__in=valid_event_ids, order_status="p"
            )
        else:
            base_qs = AnalyticsOrderFact.objects.filter(
                event=event, order_status="p"
            )

        base_qs = apply_dashboard_filters(base_qs, filter_data)

        # ── Overview KPIs ────────────────────────────────────────────────────
        agg = base_qs.aggregate(
            total_revenue=Sum("total_gross"),
            total_orders=Count("id"),
            total_tickets=Sum("ticket_count"),
            avg_order_value=Avg("total_gross"),
            repeat_count=Count("id", filter=Q(is_repeat_buyer=True)),
            caravan_count=Count("id", filter=Q(has_caravan_pass=True)),
            high_score_count=Count("id", filter=Q(predicted_repeat_probability__gte=60)),
        )

        total_orders = agg["total_orders"] or 0
        repeat_count = agg["repeat_count"] or 0
        caravan_count = agg["caravan_count"] or 0
        high_score_count = agg["high_score_count"] or 0

        repeat_pct = round(repeat_count / total_orders * 100, 1) if total_orders else 0
        caravan_pct = round(caravan_count / total_orders * 100, 1) if total_orders else 0
        high_score_pct = round(high_score_count / total_orders * 100, 1) if total_orders else 0

        # ── Demographics ──────────────────────────────────────────────────────
        country_breakdown_raw = list(
            base_qs.values("country_code")
            .annotate(
                count=Count("id"),
                revenue=Sum("total_gross"),
                aov=Avg("total_gross")
            )
            .order_by("-count", "country_code")[:20]
        )

        country_breakdown = []
        for r in country_breakdown_raw:
            code = r["country_code"].upper()
            if not code:
                country_breakdown.append({
                    "country_code": "XX",
                    "country_name": "Unknown",
                    "flag": "❓",
                    "count": r["count"],
                    "revenue": float(r["revenue"] or 0),
                    "aov": float(r["aov"] or 0),
                })
                continue
                
            c = pycountry.countries.get(alpha_2=code)
            country_breakdown.append({
                "country_code": code,
                "country_name": c.name if c else code,
                "flag": _country_flag(code),
                "count": r["count"],
                "revenue": float(r["revenue"] or 0),
                "aov": float(r["aov"] or 0),
            })

        age_breakdown = list(
            base_qs.exclude(age_range="")
            .values("age_range")
            .annotate(count=Count("id"))
            .order_by("age_range")
        )
        age_confirmed_count = base_qs.filter(is_age_confirmed=True).count()

        # ── Languages & Payments ──────────────────────────────────────────────
        language_breakdown = list(
            base_qs.exclude(language="")
            .values("language")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )
        payment_breakdown = list(
            base_qs.exclude(payment_provider="")
            .values("payment_provider")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        unfiltered_qs = AnalyticsOrderFact.objects.filter(event=event)
        refund_count = unfiltered_qs.filter(is_refunded=True).count()
        total_all_orders = unfiltered_qs.count()
        refund_pct = round(refund_count / total_all_orders * 100, 1) if total_all_orders else 0

        # ── Caravan breakdown ─────────────────────────────────────────────────
        van_length_breakdown = list(
            base_qs.filter(has_caravan_pass=True)
            .exclude(camper_van_length_bucket="")
            .values("camper_van_length_bucket")
            .annotate(count=Count("id"))
            .order_by("camper_van_length_bucket")
        )

        # ── Sales trends ──────────────────────────────────────────────────────
        daily_sales = list(
            base_qs.annotate(day=TruncDay("order_datetime"))
            .values("day")
            .annotate(revenue=Sum("total_gross"), orders=Count("id"))
            .order_by("day")
        )

        hourly_heatmap = list(
            base_qs.annotate(hour=TruncHour("order_datetime"))
            .values("hour")
            .annotate(orders=Count("id"))
            .order_by("hour")
        )

        # ── Sales Velocity & Pacing ───────────────────────────────────────────
        event_dict = {
            e.id: e for e in Event.objects.filter(
                id__in=base_qs.values_list("event_id", flat=True).distinct()
            ).select_related("analytics_config")
        }

        pacing_orders = base_qs.values(
            "event_id", "order_datetime", "total_gross"
        ).order_by("order_datetime").iterator(chunk_size=500)

        pacing_data_by_edition = defaultdict(lambda: defaultdict(float))

        for order in pacing_orders:
            evt = event_dict.get(order["event_id"])
            if not evt or not evt.date_from:
                continue
            
            # Days until the event start date
            days_until = (evt.date_from.date() - order["order_datetime"].date()).days
            
            # Group by series edition year, or just event name
            edition_label = f"{evt.name} ({evt.analytics_config.edition_year})" if hasattr(evt, "analytics_config") else evt.name
            pacing_data_by_edition[edition_label][days_until] += float(order["total_gross"])

        pacing_datasets = []
        for edition_label, days_data in pacing_data_by_edition.items():
            sorted_days = sorted(days_data.keys(), reverse=True)
            cumulative = 0
            xy_data = []
            
            for day in sorted_days:
                # We cap at say 400 days out to prevent early testing outliers from skewing the chart too far back
                if day > 400: continue
                
                cumulative += days_data[day]
                # In chart.js x-axis, -day means "X days before event"
                xy_data.append({"x": -day, "y": round(cumulative, 2)})
            
            pacing_datasets.append({
                "label": edition_label,
                "data": xy_data
            })

        # ── Buyer Personas (Purchase Timing) ──────────────────────────────────
        # Windows are computed dynamically from each event's sale start date:
        #   Early Bird  = orders placed in the first 25% of the sale window
        #   Last Minute = orders placed in the last 30 days before the event
        #   Regular     = everything in between
        LAST_MINUTE_DAYS = 30
        LAST_MINUTE_KEY = f"Last Minute (last {LAST_MINUTE_DAYS} days)"

        # Min order_datetime per event (= when tickets first went on sale)
        sale_starts = {
            row["event_id"]: row["sale_start"]
            for row in base_qs.values("event_id").annotate(sale_start=Min("order_datetime"))
        }

        persona_breakdown = {
            "Early Bird": {"count": 0, "revenue": 0, "repeats": 0, "addons": 0},
            "Regular":    {"count": 0, "revenue": 0, "repeats": 0, "addons": 0},
            LAST_MINUTE_KEY: {"count": 0, "revenue": 0, "repeats": 0, "addons": 0},
        }

        # Track computed cutoffs per event so the template can show them
        persona_cutoffs = {}

        persona_orders = base_qs.values(
            "event_id", "order_datetime", "total_gross", "is_repeat_buyer", "has_caravan_pass"
        ).iterator(chunk_size=500)

        for p in persona_orders:
            evt = event_dict.get(p["event_id"])
            if not evt or not evt.date_from:
                continue

            event_date = evt.date_from.date()
            order_date = p["order_datetime"].date()
            days_before = (event_date - order_date).days

            sale_start = sale_starts.get(p["event_id"])
            if sale_start:
                sale_start_date = sale_start.date() if hasattr(sale_start, "date") else sale_start
                sale_window = max(1, (event_date - sale_start_date).days)
                # First 25% of the sale window, at least 30 days
                early_bird_window = max(30, sale_window // 4)
                # "days before event" threshold: orders with more days remaining are Early Bird
                early_bird_cutoff = sale_window - early_bird_window
            else:
                sale_window = 0
                early_bird_window = 0
                early_bird_cutoff = 180  # fallback

            if p["event_id"] not in persona_cutoffs:
                persona_cutoffs[p["event_id"]] = {
                    "sale_window": sale_window,
                    "early_bird_days": early_bird_window,
                    "early_bird_cutoff": early_bird_cutoff,
                }

            if days_before > early_bird_cutoff:
                key = "Early Bird"
            elif days_before <= LAST_MINUTE_DAYS:
                key = LAST_MINUTE_KEY
            else:
                key = "Regular"

            persona_breakdown[key]["count"] += 1
            persona_breakdown[key]["revenue"] += float(p["total_gross"] or 0)
            if p["is_repeat_buyer"]:
                persona_breakdown[key]["repeats"] += 1
            if p["has_caravan_pass"]:
                persona_breakdown[key]["addons"] += 1

        for k, v in persona_breakdown.items():
            cnt = v["count"]
            v["aov"] = round(v["revenue"] / cnt, 2) if cnt else 0
            v["repeat_pct"] = round(v["repeats"] / cnt * 100, 1) if cnt else 0
            v["addon_pct"] = round(v["addons"] / cnt * 100, 1) if cnt else 0

        # Build a human-readable window description for the current event
        current_cutoffs = persona_cutoffs.get(event.id)
        if current_cutoffs and current_cutoffs["sale_window"]:
            persona_window_desc = (
                f"Early Bird: first {current_cutoffs['early_bird_days']} days of sales · "
                f"Last Minute: last {LAST_MINUTE_DAYS} days before event"
            )
        else:
            persona_window_desc = f"Last Minute: last {LAST_MINUTE_DAYS} days before event"


        # ── Repeat buyers ─────────────────────────────────────────────────────
        from_last_edition = base_qs.filter(repeat_from_last_edition=True).count()
        from_any_previous = base_qs.filter(repeat_from_any_previous=True).count()
        new_buyers = total_orders - repeat_count

        prob_distribution = list(
            base_qs.values("predicted_repeat_probability")
            .annotate(count=Count("id"))
            .order_by("predicted_repeat_probability")
        )

        # ── Cohort matrix (series-level) ──────────────────────────────────────
        cohort_matrix = {}
        cohort_sizes = {}
        if config.series:
            cohort_matrix = build_cohort_matrix(
                config.series.slug, event.organizer_id
            )
            cohort_sizes = get_cohort_sizes(config.series.slug, event.organizer_id)

        # ── Last synced ───────────────────────────────────────────────────────
        last_synced = (
            AnalyticsOrderFact.objects.filter(event=event)
            .aggregate(Max("updated_at"))["updated_at__max"]
        )

        # ── Ticket insights ───────────────────────────────────────────────────
        ticket_qs = AnalyticsTicketFact.objects.filter(event=event, order_fact__in=base_qs)
        ticket_breakdown = list(
            ticket_qs.filter(is_addon=False)
            .values("item_id", "item_name")
            .annotate(
                count=Count("id"),
                revenue=Sum("price"),
                avg_price=Avg("price"),
            )
            .order_by("-revenue")
        )
        addon_breakdown = list(
            ticket_qs.filter(is_addon=True)
            .values("item_id", "item_name")
            .annotate(count=Count("id"), revenue=Sum("price"))
            .order_by("-count")
        )
        for a in addon_breakdown:
            a["attach_rate"] = round(a["count"] / total_orders * 100, 1) if total_orders else 0
        group_order_count = base_qs.filter(is_group_order=True).count()
        group_order_pct = round(group_order_count / total_orders * 100, 1) if total_orders else 0

        # ── Add-on attach rates by segment ────────────────────────────────────
        addon_by_segment = []
        if addon_breakdown:
            repeat_orders = base_qs.filter(is_repeat_buyer=True)
            new_orders = base_qs.filter(is_repeat_buyer=False)
            repeat_total = repeat_orders.count()
            new_total = new_orders.count()

            repeat_addon_count = AnalyticsTicketFact.objects.filter(
                event=event, is_addon=True, order_fact__in=repeat_orders
            ).values("order_fact").distinct().count()
            new_addon_count = AnalyticsTicketFact.objects.filter(
                event=event, is_addon=True, order_fact__in=new_orders
            ).values("order_fact").distinct().count()

            addon_by_segment.append({
                "segment": "Returning Buyers",
                "total": repeat_total,
                "with_addon": repeat_addon_count,
                "attach_rate": round(repeat_addon_count / repeat_total * 100, 1) if repeat_total else 0,
            })
            addon_by_segment.append({
                "segment": "New Buyers",
                "total": new_total,
                "with_addon": new_addon_count,
                "attach_rate": round(new_addon_count / new_total * 100, 1) if new_total else 0,
            })

            # By age range
            for age in ["18-24", "25-34", "35-44", "45-54", "55-64", "65+"]:
                age_orders = base_qs.filter(age_range=age)
                age_total = age_orders.count()
                if age_total == 0:
                    continue
                age_addon_count = AnalyticsTicketFact.objects.filter(
                    event=event, is_addon=True, order_fact__in=age_orders
                ).values("order_fact").distinct().count()
                addon_by_segment.append({
                    "segment": f"Age {age}",
                    "total": age_total,
                    "with_addon": age_addon_count,
                    "attach_rate": round(age_addon_count / age_total * 100, 1) if age_total else 0,
                })

        # ── Secondary market tracking ─────────────────────────────────────────
        try:
            secondary_market = get_name_change_stats(event)
        except Exception:
            logger.exception("analytics: secondary market stats failed for %s", event.slug)
            secondary_market = {
                "total_name_changes": 0, "orders_with_changes": 0,
                "total_orders": 0, "name_change_rate": 0.0, "changes_by_month": [],
            }

        # Cross-edition name change comparison (only if part of a series)
        name_changes_by_edition = []
        if config.series:
            try:
                name_changes_by_edition = get_name_changes_by_edition(
                    config.series, event.organizer
                )
            except Exception:
                logger.exception("analytics: cross-edition name changes failed for %s", event.slug)

        # ── Editions count (for filter bar label) ─────────────────────────────
        editions_available_count = Event.objects.filter(
            organizer=request.organizer,
            analytics_config__isnull=False,
        ).count()

        # ── Serialise chart data to JSON ──────────────────────────────────────
        ctx.update(
            {
                "config": config,
                "filter_form": filter_form,
                "has_data": has_data,
                "can_resync": can_resync,
                "last_synced": last_synced,
                "resync_url": resync_url,
                "no_results_from_filter": total_orders == 0 and bool(filter_data),
                # KPIs
                "total_revenue": agg["total_revenue"] or 0,
                "total_orders": total_orders,
                "total_tickets": agg["total_tickets"] or 0,
                "avg_order_value": agg["avg_order_value"] or 0,
                "repeat_count": repeat_count,
                "repeat_pct": repeat_pct,
                "high_score_count": high_score_count,
                "high_score_pct": high_score_pct,
                "caravan_count": caravan_count,
                "caravan_pct": caravan_pct,
                # New KPIs
                "refund_count": refund_count,
                "refund_pct": refund_pct,
                # Demographics
                "country_breakdown": country_breakdown,
                "age_breakdown": age_breakdown,
                "age_confirmed_count": age_confirmed_count,
                "van_length_breakdown": van_length_breakdown,
                # Chart JSON
                "pacing_chart_json": _to_json(pacing_datasets),
                "country_chart_json": _to_json(
                    {
                        "labels": [f"{r['flag']} {r['country_code']}" for r in country_breakdown],
                        "data": [r["count"] for r in country_breakdown],
                    }
                ),
                "age_chart_json": _to_json(
                    {
                        "labels": [r["age_range"] for r in age_breakdown],
                        "data": [r["count"] for r in age_breakdown],
                    }
                ),
                "daily_sales_json": _to_json(
                    {
                        "labels": [r["day"].strftime("%Y-%m-%d") for r in daily_sales],
                        "revenue": [float(r["revenue"] or 0) for r in daily_sales],
                        "orders": [r["orders"] for r in daily_sales],
                    }
                ),
                "hourly_heatmap_json": _to_json(
                    [
                        {"hour": r["hour"].strftime("%Y-%m-%dT%H:00"), "orders": r["orders"]}
                        for r in hourly_heatmap
                    ]
                ),
                "prob_distribution_json": _to_json(
                    {
                        "labels": [r["predicted_repeat_probability"] for r in prob_distribution],
                        "data": [r["count"] for r in prob_distribution],
                    }
                ),
                "van_length_json": _to_json(
                    {
                        "labels": [r["camper_van_length_bucket"] for r in van_length_breakdown],
                        "data": [r["count"] for r in van_length_breakdown],
                    }
                ),
                "language_chart_json": _to_json(
                    {
                        "labels": [r["language"].upper() for r in language_breakdown],
                        "data": [r["count"] for r in language_breakdown],
                    }
                ),
                "payment_chart_json": _to_json(
                    {
                        "labels": [r["payment_provider"] for r in payment_breakdown],
                        "data": [r["count"] for r in payment_breakdown],
                    }
                ),
                # Repeat buyers
                "buyer_stats_json": _to_json(
                    {"new_buyers": new_buyers, "repeat_count": repeat_count}
                ),
                "new_buyers": new_buyers,
                "from_last_edition": from_last_edition,
                "from_any_previous": from_any_previous,
                # Cohort
                "cohort_matrix": cohort_matrix,
                "cohort_sizes": cohort_sizes,
                "cohort_years": sorted(cohort_matrix.keys()) if cohort_matrix else [],
                "all_cohort_years": sorted(cohort_sizes.keys()) if cohort_sizes else [],
                # Tickets
                "ticket_breakdown": ticket_breakdown,
                "addon_breakdown": addon_breakdown,
                "group_order_count": group_order_count,
                "group_order_pct": group_order_pct,
                # Personas
                "persona_breakdown": persona_breakdown,
                "persona_window_desc": persona_window_desc,
                # Add-on segmentation
                "addon_by_segment": addon_by_segment,
                # Secondary market
                "secondary_market": secondary_market,
                "secondary_market_chart_json": _to_json(
                    {
                        "labels": [r["month"] for r in secondary_market["changes_by_month"]],
                        "data": [r["count"] for r in secondary_market["changes_by_month"]],
                    }
                ),
                "name_changes_by_edition_json": _to_json(
                    {
                        "labels": [str(r["edition_year"]) for r in name_changes_by_edition],
                        "rates": [r["name_change_rate"] for r in name_changes_by_edition],
                        "orders": [r["orders_with_changes"] for r in name_changes_by_edition],
                    }
                ),
                # Filter bar
                "editions_available_count": editions_available_count,
            }
        )
        return ctx


# ── Event Configuration ───────────────────────────────────────────────────────

class EventConfigView(EventPermissionRequiredMixin, FormView):
    """Configure analytics series + edition year for an event."""
    permission = "can_change_event_settings"
    template_name = "pretix_event_analytics/config.html"
    form_class = EventAnalyticsConfigForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["event"] = self.request.event
        import datetime
        fallback_year = (
            self.request.event.date_from.year
            if self.request.event.date_from
            else datetime.date.today().year
        )
        instance, _ = EventAnalyticsConfig.objects.get_or_create(
            event=self.request.event,
            defaults={"edition_year": fallback_year},
        )
        kwargs["instance"] = instance
        return kwargs

    def form_valid(self, form):
        form.save()
        messages.success(self.request, _("Analytics configuration saved."))
        return redirect(
            reverse(
                "plugins:pretix_event_analytics:dashboard",
                kwargs={
                    "organizer": self.request.organizer.slug,
                    "event": self.request.event.slug,
                },
            )
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["series_create_url"] = reverse(
            "plugins:pretix_event_analytics:series_create",
            kwargs={"organizer": self.request.organizer.slug},
        )
        ctx["series_list_url"] = reverse(
            "plugins:pretix_event_analytics:series_list",
            kwargs={"organizer": self.request.organizer.slug},
        )
        # Analytics status for the status panel
        qs = AnalyticsOrderFact.objects.filter(event=self.request.event)
        ctx["analytics_order_count"] = qs.count()
        ctx["analytics_last_synced"] = qs.aggregate(last=Max("updated_at"))["last"]
        ctx["has_analytics_data"] = qs.exists()
        return ctx


# ── Series Management (organizer level) ───────────────────────────────────────

class SeriesListView(OrganizerPermissionRequiredMixin, ListView):
    permission = "can_change_organizer_settings"
    template_name = "pretix_event_analytics/series_list.html"
    context_object_name = "series_list"

    def get_queryset(self):
        return EventSeries.objects.filter(
            organizer=self.request.organizer
        ).prefetch_related("event_configs__event").order_by("name")


class SeriesCreateView(OrganizerPermissionRequiredMixin, FormView):
    permission = "can_change_organizer_settings"
    template_name = "pretix_event_analytics/series_form.html"
    form_class = EventSeriesForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["organizer"] = self.request.organizer
        return kwargs

    def form_valid(self, form):
        series = form.save()
        messages.success(self.request, _('Series "%s" created.') % series.name)
        return redirect(
            reverse(
                "plugins:pretix_event_analytics:series_list",
                kwargs={"organizer": self.request.organizer.slug},
            )
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["action"] = _("Create series")
        return ctx


class SeriesEditView(OrganizerPermissionRequiredMixin, UpdateView):
    permission = "can_change_organizer_settings"
    template_name = "pretix_event_analytics/series_form.html"
    form_class = EventSeriesForm
    model = EventSeries
    pk_url_kwarg = "pk"

    def get_queryset(self):
        return EventSeries.objects.filter(organizer=self.request.organizer)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["organizer"] = self.request.organizer
        return kwargs

    def get_success_url(self):
        return reverse(
            "plugins:pretix_event_analytics:series_list",
            kwargs={"organizer": self.request.organizer.slug},
        )

    def form_valid(self, form):
        form.save()
        messages.success(self.request, _("Series updated."))
        return redirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["action"] = _("Edit series")
        return ctx


class SeriesDeleteView(OrganizerPermissionRequiredMixin, DeleteView):
    permission = "can_change_organizer_settings"
    template_name = "pretix_event_analytics/series_confirm_delete.html"
    model = EventSeries
    pk_url_kwarg = "pk"

    def get_queryset(self):
        return EventSeries.objects.filter(organizer=self.request.organizer)

    def get_success_url(self):
        return reverse(
            "plugins:pretix_event_analytics:series_list",
            kwargs={"organizer": self.request.organizer.slug},
        )

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        messages.success(request, _('Series "%s" deleted.') % obj.name)
        return super().delete(request, *args, **kwargs)


# ── Export views (stubs — filled by exporters.py) ─────────────────────────────

class ExportCSVView(EventPermissionRequiredMixin, TemplateView):
    permission = "can_view_orders"

    def get(self, request, *args, **kwargs):
        from .exporters import export_csv
        return export_csv(request, request.event)


class ExportPDFView(EventPermissionRequiredMixin, TemplateView):
    permission = "can_view_orders"

    def get(self, request, *args, **kwargs):
        from .exporters import export_pdf
        return export_pdf(request, request.event)


# ── Resync trigger ────────────────────────────────────────────────────────────

class TriggerResyncView(EventPermissionRequiredMixin, View):
    """
    Queue an async full resync for this event.
    POST-only; redirects back to dashboard on success.
    Requires can_change_event_settings so only admins can trigger it.
    """
    permission = "can_change_event_settings"

    def post(self, request, *args, **kwargs):
        from django.core.cache import cache

        from .tasks import trigger_event_resync

        # Rate limit: prevent triggering a second resync while one is in progress.
        # Lock expires after 10 minutes — enough time for even a large resync.
        lock_key = f"analytics_resync_lock_{request.event.pk}"
        if cache.get(lock_key):
            messages.warning(
                request,
                _("A resync is already running for this event. Please wait a few minutes."),
            )
            return redirect(
                reverse(
                    "plugins:pretix_event_analytics:dashboard",
                    kwargs={
                        "organizer": request.organizer.slug,
                        "event": request.event.slug,
                    },
                )
            )
        cache.set(lock_key, True, timeout=600)

        include_checkin = request.POST.get("include_checkin") == "1"
        trigger_event_resync.apply_async(
            args=[request.event.pk],
            kwargs={"include_checkin": include_checkin},
        )
        messages.success(
            request,
            _("Analytics resync queued. The dashboard will update shortly."),
        )
        return redirect(
            reverse(
                "plugins:pretix_event_analytics:dashboard",
                kwargs={
                    "organizer": request.organizer.slug,
                    "event": request.event.slug,
                },
            )
        )
