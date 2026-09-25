"""
URL configuration for pretix_event_analytics.

app_name is required for the 'plugins:pretix_event_analytics:*' URL namespace.
"""
from django.urls import re_path

from . import views

app_name = "pretix_event_analytics"

EVENT = r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/analytics/"
ORG = r"^control/organizer/(?P<organizer>[^/]+)/analytics/"

urlpatterns = [
    re_path(EVENT + r"$", views.DashboardView.as_view(), name="dashboard"),
    re_path(EVENT + r"sales/$", views.SalesView.as_view(), name="sales"),
    re_path(EVENT + r"audience/$", views.AudienceView.as_view(), name="audience"),
    re_path(EVENT + r"loyalty/$", views.LoyaltyView.as_view(), name="loyalty"),
    re_path(EVENT + r"tickets/$", views.TicketsView.as_view(), name="tickets"),
    re_path(EVENT + r"operations/$", views.OperationsView.as_view(), name="operations"),
    re_path(EVENT + r"resale/$", views.ResaleView.as_view(), name="resale"),
    re_path(EVENT + r"config/$", views.EventConfigView.as_view(), name="config"),
    re_path(EVENT + r"export/(?P<kind>csv|tickets|loyalty|pdf)/$", views.ExportView.as_view(), name="export"),
    re_path(EVENT + r"resync/$", views.TriggerResyncView.as_view(), name="trigger_resync"),

    re_path(ORG + r"series/$", views.SeriesListView.as_view(), name="series_list"),
    re_path(ORG + r"series/create/$", views.SeriesCreateView.as_view(), name="series_create"),
    re_path(ORG + r"series/(?P<pk>\d+)/$", views.SeriesDetailView.as_view(), name="series_detail"),
    re_path(ORG + r"series/(?P<pk>\d+)/edit/$", views.SeriesEditView.as_view(), name="series_edit"),
    re_path(ORG + r"series/(?P<pk>\d+)/delete/$", views.SeriesDeleteView.as_view(), name="series_delete"),
    re_path(ORG + r"series/(?P<pk>\d+)/resync/$", views.SeriesResyncView.as_view(), name="series_resync"),
    re_path(ORG + r"series/(?P<pk>\d+)/legacy/$", views.LegacyImportView.as_view(), name="legacy_import"),
    re_path(ORG + r"series/(?P<pk>\d+)/legacy/(?P<legacy_pk>\d+)/delete/$", views.LegacyDeleteView.as_view(),
            name="legacy_delete"),
]
