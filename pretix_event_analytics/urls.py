"""
URL configuration for pretix_event_analytics.

Pretix expects two separate lists:
  urlpatterns       — event-level routes
  organizer_patterns — organizer-level routes

app_name is required for the 'plugins:pretix_event_analytics:*' URL namespace to work.
"""
from django.urls import re_path

from . import views

app_name = "pretix_event_analytics"

# Event and Organizer URLs must be placed in urlpatterns with the full prefix
# as Pretix expects this when registering plugin URLs.
urlpatterns = [
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/analytics/$",
        views.DashboardView.as_view(),
        name="dashboard",
    ),
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/analytics/config/$",
        views.EventConfigView.as_view(),
        name="config",
    ),
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/analytics/export/csv/$",
        views.ExportCSVView.as_view(),
        name="export_csv",
    ),
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/analytics/export/pdf/$",
        views.ExportPDFView.as_view(),
        name="export_pdf",
    ),
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/analytics/resync/$",
        views.TriggerResyncView.as_view(),
        name="trigger_resync",
    ),
    re_path(
        r"^control/organizer/(?P<organizer>[^/]+)/analytics/series/$",
        views.SeriesListView.as_view(),
        name="series_list",
    ),
    re_path(
        r"^control/organizer/(?P<organizer>[^/]+)/analytics/series/create/$",
        views.SeriesCreateView.as_view(),
        name="series_create",
    ),
    re_path(
        r"^control/organizer/(?P<organizer>[^/]+)/analytics/series/(?P<pk>\d+)/edit/$",
        views.SeriesEditView.as_view(),
        name="series_edit",
    ),
    re_path(
        r"^control/organizer/(?P<organizer>[^/]+)/analytics/series/(?P<pk>\d+)/delete/$",
        views.SeriesDeleteView.as_view(),
        name="series_delete",
    ),
]
