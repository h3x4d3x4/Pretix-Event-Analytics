"""
Filter helpers for AnalyticsOrderFact querysets.

All filtering operates exclusively on AnalyticsOrderFact — no joins back to
raw Pretix order tables.  This is the performance boundary.
"""
from django.db.models import QuerySet


def apply_dashboard_filters(qs: QuerySet, form_data: dict) -> QuerySet:
    """
    Apply validated DashboardFilterForm data to an AnalyticsOrderFact queryset.

    :param qs: Base queryset (already scoped to correct event).
    :param form_data: Cleaned data from DashboardFilterForm.
    :returns: Filtered queryset.
    """
    if not form_data:
        return qs

    date_from = form_data.get("date_from")
    date_to = form_data.get("date_to")
    country = (form_data.get("country") or "").strip().upper()
    age_range = form_data.get("age_range")
    repeat_only = form_data.get("repeat_only")
    include_refunded = form_data.get("include_refunded")
    has_caravan = form_data.get("has_caravan")
    ticket_type_list = form_data.get("ticket_type") or []

    if date_from:
        qs = qs.filter(order_datetime__date__gte=date_from)
    if date_to:
        qs = qs.filter(order_datetime__date__lte=date_to)
    if country:
        qs = qs.filter(country_code=country)
    if age_range:
        qs = qs.filter(age_range=age_range)
    if repeat_only:
        qs = qs.filter(is_repeat_buyer=True)
    if not include_refunded:
        qs = qs.filter(is_refunded=False)
    if has_caravan:
        qs = qs.filter(has_caravan_pass=True)
    if ticket_type_list:
        qs = qs.filter(ticket_facts__item_name__in=ticket_type_list).distinct()

    return qs
