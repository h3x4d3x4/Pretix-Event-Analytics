"""
Template helpers for the analytics pages.

  qs_with      — current query string with some keys replaced
  delta_class  — "up" / "down" CSS class for a signed number
  abs_value    — absolute value (for "▲ 12%" style deltas)
"""
from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def qs_with(context, **kwargs):
    """Current query string with some keys replaced: ?{% qs_with granularity='week' %}"""
    q = context["request"].GET.copy()
    for k, v in kwargs.items():
        if v is None or v == "":
            q.pop(k, None)
        else:
            q[k] = v
    return q.urlencode()


@register.filter
def delta_class(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    return "up" if v > 0 else "down" if v < 0 else ""


@register.filter
def abs_value(value):
    try:
        return abs(float(value))
    except (TypeError, ValueError):
        return value
