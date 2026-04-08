"""
Django admin registrations for analytics models.
Provides a fallback interface for debugging and manual data inspection.
Day-to-day use is via the Pretix control panel dashboard.
"""
from django.contrib import admin

from .models import AnalyticsOrderFact, AnalyticsTicketFact, EventAnalyticsConfig, EventSeries


@admin.register(EventSeries)
class EventSeriesAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "organizer", "edition_count", "created_at")
    list_filter = ("organizer",)
    search_fields = ("name", "slug")
    readonly_fields = ("created_at", "updated_at")

    def edition_count(self, obj):
        return obj.event_configs.count()
    edition_count.short_description = "Editions"


@admin.register(EventAnalyticsConfig)
class EventAnalyticsConfigAdmin(admin.ModelAdmin):
    list_display = ("event", "series", "edition_year", "home_country", "is_active")
    list_filter = ("series", "edition_year", "is_active")
    search_fields = ("event__slug", "series__name")
    autocomplete_fields = ("series",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(AnalyticsOrderFact)
class AnalyticsOrderFactAdmin(admin.ModelAdmin):
    list_display = (
        "order_code",
        "event",
        "edition_year",
        "order_datetime",
        "total_gross",
        "country_code",
        "age_range",
        "is_repeat_buyer",
        "repeat_count",
        "predicted_repeat_probability",
        "has_caravan_pass",
    )
    list_filter = (
        "event",
        "edition_year",
        "is_repeat_buyer",
        "is_refunded",
        "has_caravan_pass",
        "country_code",
        "age_range",
    )
    search_fields = ("order_code", "series_slug")
    readonly_fields = (
        "repeat_hash",
        "created_at",
        "updated_at",
    )


@admin.register(AnalyticsTicketFact)
class AnalyticsTicketFactAdmin(admin.ModelAdmin):
    list_display = (
        "item_name",
        "variation_name",
        "event",
        "price",
        "is_addon",
        "is_caravan_pass",
        "camper_van_length_bucket",
        "age_range",
    )
    list_filter = ("event", "is_addon", "is_caravan_pass", "age_range")
    search_fields = ("item_name", "order_fact__order_code")
