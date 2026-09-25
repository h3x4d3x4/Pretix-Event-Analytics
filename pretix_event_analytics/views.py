"""
Views for the pretix_event_analytics plugin.

Event level (EventPermissionRequiredMixin):
  analytics/                 Overview          analytics/loyalty/     Loyalty
  analytics/sales/           Sales             analytics/tickets/     Tickets
  analytics/audience/        Audience          analytics/operations/  Check-in, refunds, questions
  analytics/resale/          Resale signal     analytics/config/      Settings
  analytics/export/<kind>/   CSV / PDF exports analytics/resync/      Resync (POST)

Organizer level (OrganizerPermissionRequiredMixin):
  analytics/series/…         series CRUD, series overview, legacy imports, series resync

Every dashboard page reads through ``services.reports.scope.ReportScope``
so the filter bar behaves identically everywhere.
"""
import datetime
import json
import logging
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Max
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DeleteView, FormView, ListView, TemplateView, UpdateView

from pretix.control.permissions import EventPermissionRequiredMixin, OrganizerPermissionRequiredMixin

from .forms import DashboardFilterForm, EventAnalyticsConfigForm, EventSeriesForm, LegacyImportForm
from .models import AnalyticsOrderFact, EventAnalyticsConfig, EventSeries, LegacyEdition
from ._compat import CHANGE_EVENT_SETTINGS, CHANGE_ORGANIZER_SETTINGS, VIEW_ORDERS

logger = logging.getLogger(__name__)


class _Encoder(DjangoJSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def script_json(data) -> str:
    """JSON that is safe to embed in a <script> element (product names are user input)."""
    return (json.dumps(data, cls=_Encoder)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def _event_url(request, name, **kw):
    return reverse(f"plugins:pretix_event_analytics:{name}",
                   kwargs={"organizer": request.organizer.slug, "event": request.event.slug, **kw})


# ── Dashboard pages ───────────────────────────────────────────────────────────

TABS = [
    ("dashboard", _("Overview"), "tachometer"),
    ("sales", _("Sales"), "line-chart"),
    ("audience", _("Audience"), "users"),
    ("loyalty", _("Loyalty"), "refresh"),
    ("tickets", _("Tickets"), "ticket"),
    ("operations", _("Operations"), "sign-in"),
    ("resale", _("Resale"), "exchange"),
]
FILTER_FIELDS = set(DashboardFilterForm.base_fields)


class AnalyticsPageView(EventPermissionRequiredMixin, TemplateView):
    """Base class: filter bar, tabs, header and chart plumbing."""
    permission = VIEW_ORDERS
    tab = "dashboard"
    section = None  # module in services.reports with build(scope)

    def get(self, request, *args, **kwargs):
        if not EventAnalyticsConfig.objects.filter(event=request.event).exists():
            messages.info(request, _("Please configure analytics for this event before viewing the dashboard."))
            return redirect(_event_url(request, "config"))
        return super().get(request, *args, **kwargs)

    def build(self, scope):
        import importlib
        mod = importlib.import_module(f"pretix_event_analytics.services.reports.{self.section}")
        return mod.build(scope)

    def get_context_data(self, **kwargs):
        from .services.reports.scope import ReportScope

        ctx = super().get_context_data(**kwargs)
        request = self.request
        scope = ReportScope(request)
        data = self.build(scope) if self.section else {}

        filter_params = [(k, v) for k in request.GET for v in request.GET.getlist(k) if k in FILTER_FIELDS]
        section_params = [(k, v) for k in request.GET for v in request.GET.getlist(k) if k not in FILTER_FIELDS]
        filter_qs = urlencode(filter_params)
        charts = {}
        _collect_charts(data, "", charts)
        can_resync = request.user.has_event_permission(request.organizer, request.event,
                                                       CHANGE_EVENT_SETTINGS, request)
        facts = AnalyticsOrderFact.objects.filter(event=request.event)
        ctx.update({
            "scope": scope,
            "config": scope.config,
            "filter_form": scope.form,
            "filter_chips": scope.filter_chips(),
            "filter_qs": filter_qs,
            "filter_params": filter_params,
            "section_params": section_params,
            "section_qs": urlencode(section_params),
            "data": data,
            "charts_json": script_json(charts),
            "tabs": [{"name": n, "label": label, "icon": icon, "active": n == self.tab,
                      "url": _event_url(request, n) + (f"?{filter_qs}" if filter_qs else "")} for n, label, icon in TABS],
            "tab": self.tab,
            "can_resync": can_resync,
            "resync_url": _event_url(request, "trigger_resync"),
            "has_data": facts.exists(),
            "last_synced": facts.aggregate(m=Max("updated_at"))["m"],
            "currency": scope.currency,
            "export_qs": f"?{filter_qs}" if filter_qs else "",
        })
        return ctx


def _collect_charts(obj, path, out):
    """Pull every chart spec out of the section data, keyed by a stable id."""
    if isinstance(obj, dict):
        if "kind" in obj and "series" in obj and "labels" in obj:
            chart_id = "chart-" + path.strip("-")
            out[chart_id] = obj
            obj["id"] = chart_id
            return
        for k, v in obj.items():
            _collect_charts(v, f"{path}-{k}", out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _collect_charts(v, f"{path}-{i}", out)


class DashboardView(AnalyticsPageView):
    template_name = "pretix_event_analytics/pages/overview.html"
    tab = "dashboard"
    section = "overview"


class SalesView(AnalyticsPageView):
    template_name = "pretix_event_analytics/pages/sales.html"
    tab = "sales"
    section = "sales"


class AudienceView(AnalyticsPageView):
    template_name = "pretix_event_analytics/pages/audience.html"
    tab = "audience"
    section = "audience"


class LoyaltyView(AnalyticsPageView):
    template_name = "pretix_event_analytics/pages/loyalty.html"
    tab = "loyalty"
    section = "loyalty"


class TicketsView(AnalyticsPageView):
    template_name = "pretix_event_analytics/pages/tickets.html"
    tab = "tickets"
    section = "tickets"


class OperationsView(AnalyticsPageView):
    template_name = "pretix_event_analytics/pages/operations.html"
    tab = "operations"
    section = "operations"


class ResaleView(AnalyticsPageView):
    template_name = "pretix_event_analytics/pages/resale.html"
    tab = "resale"
    section = "market"


# ── Event Configuration ───────────────────────────────────────────────────────

class EventConfigView(EventPermissionRequiredMixin, FormView):
    """Configure series, edition year, targets and tracked questions for an event."""
    permission = CHANGE_EVENT_SETTINGS
    template_name = "pretix_event_analytics/config.html"
    form_class = EventAnalyticsConfigForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["event"] = self.request.event
        fallback_year = (self.request.event.date_from.year if self.request.event.date_from
                         else datetime.date.today().year)
        instance, _created = EventAnalyticsConfig.objects.get_or_create(
            event=self.request.event, defaults={"edition_year": fallback_year},
        )
        kwargs["instance"] = instance
        return kwargs

    def form_valid(self, form):
        before = EventAnalyticsConfig.objects.select_related("series").get(pk=form.instance.pk)
        old_series = before.series
        old_questions = list(before.tracked_question_ids or [])
        data = form.cleaned_data
        changed = (
            old_series != data.get("series")
            or before.edition_year != data.get("edition_year")
            or before.is_active != data.get("is_active")
            or before.home_country != data.get("home_country")
        )
        config = form.save()
        if changed:
            # Series membership, ordering or scoring inputs moved: re-resolve
            # returning buyers for the old and the new series.
            from .services.people import queue_recompute, scope_of
            queue_recompute(*scope_of(config.event))
            if old_series and old_series != config.series:
                queue_recompute(old_series.organizer_id, old_series.slug)
        else:
            from .services.versioning import bump
            bump(config.event.organizer_id)
        messages.success(self.request, _("Analytics configuration saved."))
        if sorted(old_questions) != sorted(config.tracked_question_ids):
            messages.info(self.request, _("Run a resync to apply the new question selection to existing orders."))
        return redirect(_event_url(self.request, "dashboard"))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        org = self.request.organizer.slug
        ctx["series_create_url"] = reverse("plugins:pretix_event_analytics:series_create", kwargs={"organizer": org})
        ctx["series_list_url"] = reverse("plugins:pretix_event_analytics:series_list", kwargs={"organizer": org})
        qs = AnalyticsOrderFact.objects.filter(event=self.request.event)
        ctx["analytics_order_count"] = qs.count()
        ctx["analytics_last_synced"] = qs.aggregate(last=Max("updated_at"))["last"]
        ctx["has_analytics_data"] = ctx["analytics_order_count"] > 0
        return ctx


# ── Series Management (organizer level) ───────────────────────────────────────

class SeriesMixin(OrganizerPermissionRequiredMixin):
    permission = CHANGE_ORGANIZER_SETTINGS

    def series_url(self, name, **kw):
        return reverse(f"plugins:pretix_event_analytics:{name}", kwargs={"organizer": self.request.organizer.slug, **kw})


class SeriesListView(SeriesMixin, ListView):
    template_name = "pretix_event_analytics/series_list.html"
    context_object_name = "series_list"

    def get_queryset(self):
        return (EventSeries.objects.filter(organizer=self.request.organizer)
                .prefetch_related("event_configs__event", "legacy_editions").order_by("name"))


class SeriesCreateView(SeriesMixin, FormView):
    template_name = "pretix_event_analytics/series_form.html"
    form_class = EventSeriesForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["organizer"] = self.request.organizer
        return kwargs

    def form_valid(self, form):
        series = form.save()
        messages.success(self.request, _('Series "%s" created.') % series.name)
        return redirect(self.series_url("series_list"))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["action"] = _("Create series")
        return ctx


class SeriesEditView(SeriesMixin, UpdateView):
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
        return self.series_url("series_list")

    def form_valid(self, form):
        form.save()
        messages.success(self.request, _("Series updated."))
        return redirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["action"] = _("Edit series")
        return ctx


class SeriesDeleteView(SeriesMixin, DeleteView):
    template_name = "pretix_event_analytics/series_confirm_delete.html"
    model = EventSeries
    pk_url_kwarg = "pk"

    def get_queryset(self):
        return EventSeries.objects.filter(organizer=self.request.organizer)

    def get_success_url(self):
        return self.series_url("series_list")

    def form_valid(self, form):
        obj = self.get_object()
        messages.success(self.request, _('Series "%s" deleted.') % obj.name)
        event_ids = list(obj.event_configs.values_list("event_id", flat=True))
        response = super().form_valid(form)
        # Former editions are now standalone: nobody is "returning" any more.
        from .services.people import queue_recompute
        for ev_id in event_ids:
            queue_recompute(self.request.organizer.pk, "", ev_id)
        return response


class SeriesDetailView(SeriesMixin, TemplateView):
    """
    Every edition of a series side by side + loyalty across the series.
    Same permission as series management (organizer settings), since it
    aggregates orders of every edition.
    """
    template_name = "pretix_event_analytics/series_detail.html"

    def get_context_data(self, **kwargs):
        from django.db.models import Count

        from .services.reports import series as series_report

        ctx = super().get_context_data(**kwargs)
        series = get_object_or_404(EventSeries, organizer=self.request.organizer, pk=kwargs["pk"])
        # Organizer-settings access does not imply order access: the overview
        # aggregates orders of every edition, so require both.
        edition_ids = set(series.event_configs.values_list("event_id", flat=True))
        allowed = set(self.request.user.get_events_with_permission(VIEW_ORDERS, self.request)
                      .filter(pk__in=edition_ids).values_list("pk", flat=True))
        if edition_ids - allowed:
            raise PermissionDenied(_("You need order access to every edition of this series."))
        unit = self.request.GET.get("unit", "people")
        unit = unit if unit in ("people", "buyers") else "people"
        data = series_report.build(series, unit)
        charts = {}
        _collect_charts(data, "", charts)
        first_config = series.event_configs.select_related("event").first()
        ctx.update({
            "series": series,
            "data": data,
            "unit": unit,
            "charts_json": script_json(charts),
            "legacy_form": LegacyImportForm(),
            "legacy_editions": series.legacy_editions.annotate(n=Count("identities")),
            "currency": first_config.event.currency if first_config else "",
        })
        return ctx


class LegacyImportView(SeriesMixin, FormView):
    """Import a past attendee list. Addresses are hashed in memory and discarded."""
    form_class = LegacyImportForm
    template_name = "pretix_event_analytics/series_detail.html"

    def post(self, request, *args, **kwargs):
        from .services.legacy import import_legacy_list

        series = get_object_or_404(EventSeries, organizer=request.organizer, pk=kwargs["pk"])
        form = LegacyImportForm(request.POST, request.FILES)
        target = self.series_url("series_detail", pk=series.pk)
        if not form.is_valid():
            for errs in form.errors.values():
                for e in errs:
                    messages.error(request, e)
            return redirect(target)
        raw = ""
        if form.cleaned_data.get("emails_file"):
            raw = form.cleaned_data["emails_file"].read().decode("utf-8", errors="ignore")
        raw += "\n" + (form.cleaned_data.get("emails_text") or "")
        result = import_legacy_list(series, form.cleaned_data["label"], form.cleaned_data["edition_year"], raw)
        messages.success(request, _("Imported %(n)s unique addresses into “%(label)s”. The list itself was not stored.") % {
            "n": result["imported"], "label": form.cleaned_data["label"]})
        return redirect(target)


class LegacyDeleteView(SeriesMixin, View):
    def post(self, request, *args, **kwargs):
        from .services.people import queue_recompute

        le = get_object_or_404(LegacyEdition, series__organizer=request.organizer, pk=kwargs["legacy_pk"])
        series = le.series
        le.delete()
        queue_recompute(series.organizer_id, series.slug)
        messages.success(request, _("Legacy edition removed."))
        return redirect(self.series_url("series_detail", pk=series.pk))


class SeriesResyncView(SeriesMixin, View):
    """Resync every edition of a series in one background job."""
    def post(self, request, *args, **kwargs):
        from django.core.cache import cache

        from .tasks import trigger_series_resync

        series = get_object_or_404(EventSeries, organizer=request.organizer, pk=kwargs["pk"])
        key = f"analytics_series_resync_lock_{series.pk}"
        if not cache.add(key, True, timeout=300):
            messages.warning(request, _("A resync for this series was started recently. Please wait a few minutes."))
        else:
            trigger_series_resync.apply_async(args=[series.pk])
            messages.success(request, _("Series resync queued. Figures update when it finishes."))
        return redirect(self.series_url("series_detail", pk=series.pk))


# ── Exports ───────────────────────────────────────────────────────────────────

class ExportView(EventPermissionRequiredMixin, View):
    permission = VIEW_ORDERS

    def get(self, request, *args, **kwargs):
        from . import exporters

        kind = kwargs.get("kind")
        handlers = {
            "csv": exporters.export_orders_csv,
            "tickets": exporters.export_tickets_csv,
            "loyalty": exporters.export_loyalty_csv,
            "pdf": exporters.export_pdf,
        }
        if kind not in handlers:
            raise Http404()
        return handlers[kind](request, request.event)


# ── Resync trigger ────────────────────────────────────────────────────────────

class TriggerResyncView(EventPermissionRequiredMixin, View):
    """
    Queue an async full resync for this event. POST-only; redirects back.
    Requires can_change_event_settings so only admins can trigger it.
    """
    permission = CHANGE_EVENT_SETTINGS
    # Per-user throttle, independent of the per-event in-progress lock.
    RATE_LIMIT_SECONDS = 60

    def post(self, request, *args, **kwargs):
        from django.core.cache import cache

        from .tasks import trigger_event_resync

        back = redirect(request.POST.get("next") if (request.POST.get("next") or "").startswith("/control/")
                        else _event_url(request, "dashboard"))

        rate_key = f"analytics_resync_rate_{request.user.pk}_{request.event.pk}"
        if cache.get(rate_key):
            messages.warning(request, _("Please wait a moment before triggering another resync."))
            return back
        cache.set(rate_key, True, timeout=self.RATE_LIMIT_SECONDS)

        lock_key = f"analytics_resync_lock_{request.event.pk}"
        if not cache.add(lock_key, True, timeout=600):
            messages.warning(request, _("A resync is already running for this event. Please wait a few minutes."))
            return back

        trigger_event_resync.apply_async(args=[request.event.pk])
        messages.success(request, _("Analytics resync queued. The dashboard will update shortly."))
        return back
