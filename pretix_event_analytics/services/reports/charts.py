"""
Chart specs — the JSON contract between section builders and dashboard.js.

A spec is plain data; the JS renders every chart from it, adds hover
tooltips and a table view, and resolves colours from *slots* (fixed
categorical positions of a validated palette). Colour follows the entity:
builders assign slots from a stable ordering so that a filter which drops
one series never repaints the others.

    {
      "kind": "bar" | "line",
      "labels": [...],                     # category / time axis
      "series": [{"name", "data", "slot"}],
      "format": "number" | "currency" | "percent",
      "stacked": bool, "horizontal": bool,
      "cumulative": bool,                  # offer a cumulative toggle
      "x": "category" | "linear",          # linear = numeric x (days before event)
      "xTitle": str, "yTitle": str,
      "markers": [{"x": label, "text": str}],
      "highlight": int                     # series index drawn emphasised
    }
"""
from decimal import Decimal
from typing import Dict, Iterable, List, Optional

MAX_SLOTS = 7  # the 8th colour is reserved for "Other"
OTHER_SLOT = "other"


def spec(kind: str, labels: List, series: List[Dict], *, fmt: str = "number", stacked: bool = False,
         horizontal: bool = False, cumulative: bool = False, x: str = "category",
         x_title: str = "", y_title: str = "", markers: Optional[List[Dict]] = None,
         highlight: Optional[int] = None) -> Dict:
    out = {
        "kind": kind, "labels": labels, "series": series, "format": fmt, "stacked": stacked,
        "horizontal": horizontal, "cumulative": cumulative, "x": x, "xTitle": str(x_title),
        "yTitle": str(y_title),
    }
    if markers:
        out["markers"] = markers
    if highlight is not None:
        out["highlight"] = highlight
    return out


def serie(name, data, slot=0) -> Dict:
    return {"name": str(name), "data": [float(v) if v is not None else None for v in data], "slot": slot}


def assign_slots(ranked: Iterable[str]) -> Dict[str, object]:
    """First MAX_SLOTS entities get fixed slots; the rest fold into Other."""
    slots: Dict[str, object] = {}
    for i, name in enumerate(ranked):
        slots[name] = i if i < MAX_SLOTS else OTHER_SLOT
    return slots


def fold_other(rows: List[Dict], key: str, value: str, n: int = MAX_SLOTS, other_label: str = "Other") -> List[Dict]:
    """Keep the top ``n`` rows by ``value``; sum the remainder into one row."""
    rows = sorted(rows, key=lambda r: r[value] or 0, reverse=True)
    if len(rows) <= n + 1:
        return rows
    head, tail = rows[:n], rows[n:]
    other = {key: other_label}
    for k in tail[0]:
        if k == key:
            continue
        if all(isinstance(r.get(k), (int, float, Decimal)) or r.get(k) is None for r in tail):
            other[k] = sum((r.get(k) or 0) for r in tail)
    return head + [other]
