"""
Custom template filters for the analytics dashboard.

  get_item      — dict[key] safe lookup
  get_nested    — dict[key1][key2] safe nested lookup
  mul           — multiply a value (for percentage display)
  cohort_color  — map a retention % to a CSS rgba background colour
"""
from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Safe dict lookup: {{ my_dict|get_item:key }}"""
    if dictionary is None:
        return None
    return dictionary.get(key)


@register.simple_tag
def get_nested(dictionary, key1, key2):
    """
    Safe nested dict lookup: {% get_nested cohort_matrix source_year target_year %}
    Returns None if either key is missing.
    """
    if dictionary is None:
        return None
    inner = dictionary.get(key1)
    if not isinstance(inner, dict):
        return None
    return inner.get(key2)


@register.filter
def mul(value, factor):
    """Multiply a value: {{ 0.42|mul:100 }} → 42.0"""
    try:
        return float(value) * float(factor)
    except (TypeError, ValueError):
        return 0


@register.filter
def cohort_color(pct):
    """
    Map a retention percentage (0–100) to an rgba background colour.
    Green for high retention, blue for medium, light grey for low.
    Used to give the cohort heatmap table a visual gradient.
    """
    try:
        pct = max(0.0, min(100.0, float(pct)))
    except (TypeError, ValueError):
        return "rgba(245,245,245,1)"

    if pct >= 50:
        # Strong green
        intensity = min(1.0, pct / 100)
        return f"rgba(39,174,96,{intensity:.2f})"
    elif pct >= 25:
        # Blue
        intensity = min(1.0, pct / 60)
        return f"rgba(52,152,219,{intensity:.2f})"
    elif pct >= 10:
        # Pale yellow-orange
        return "rgba(243,156,18,0.6)"
    else:
        # Very light grey
        return "rgba(236,240,241,1)"


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
