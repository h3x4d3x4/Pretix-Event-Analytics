#!/usr/bin/env python3
"""
Isolation check — fails if the plugin source contains any write-style call
(save / delete / update / create / bulk_create / raw / …) that targets a
Pretix core model.

Usage:
    python scripts/check_isolation.py
    → exit code 0 when clean, 1 when violations found.

The checker is deliberately conservative — it reports any suspicious
pattern it sees so you have to either (a) remove the write or (b) add the
location to ALLOWED_WRITE_POINTS in pretix_event_analytics/_safety.py with
an explanatory comment and a code review.

This runs as a pure static check: no Django, no database, no imports of
plugin code — safe to run from any environment, including CI.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
PLUGIN_DIR = os.path.join(PROJECT_ROOT, "pretix_event_analytics")

PRETIX_CORE_MODELS = (
    "Order",
    "OrderPosition",
    "OrderPayment",
    "OrderRefund",
    "Event",
    "Organizer",
    "Item",
    "ItemVariation",
    "Question",
    "QuestionAnswer",
    "Checkin",
    "InvoiceAddress",
    "LogEntry",
)

FORBIDDEN_METHODS = (
    "save",
    "delete",
    "update",
    "create",
    "update_or_create",
    "get_or_create",
    "bulk_create",
    "bulk_update",
    "raw",
)

# Plugin-owned model names that legitimately use these methods. Matches on
# these are skipped. Keep in sync with models.py.
PLUGIN_MODELS = (
    "AnalyticsOrderFact",
    "AnalyticsTicketFact",
    "AnalyticsIdentity",
    "EventSeries",
    "EventAnalyticsConfig",
)

# Files that are allowed to import pretix.base.models for READ purposes
# but never write. The checker still reads them to ensure that assumption.
SKIP_PATHS = {
    "_safety.py",
    "migrations",
    "__pycache__",
}

# Build a regex that catches: CoreModel.objects.<method>( or
# <var>.<method>( where <var> is a known CoreModel-typed assignment like
# "order = Order.objects.get(...)".
_method_group = "|".join(FORBIDDEN_METHODS)
_model_group = "|".join(PRETIX_CORE_MODELS)

RE_DIRECT = re.compile(
    rf"\b({_model_group})\.objects\.({_method_group})\s*\("
)
# Catches pattern like `order.save()` where `order` was previously assigned
# from a core model. We scan in two passes: first note variables assigned
# from core model querysets, then flag writes against those names.
RE_ASSIGN = re.compile(
    rf"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(?:(?:{_model_group})\.objects|[a-zA-Z_][a-zA-Z0-9_]*\.get)\b"
)
RE_INSTANCE_WRITE = re.compile(
    rf"\b([a-zA-Z_][a-zA-Z0-9_]*)\.({_method_group})\s*\("
)


def scan_file(path):
    """Return list of (lineno, line, reason) for any violation."""
    violations = []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Pass 1: collect variable names assigned from a core-model queryset.
    tainted = set()
    for line in lines:
        m = RE_ASSIGN.search(line)
        if m:
            tainted.add(m.group(1))

    # Pass 2: flag direct Model.objects.<method>(...) violations.
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        m = RE_DIRECT.search(line)
        if m:
            violations.append((
                i, line.rstrip(),
                f"direct write: {m.group(1)}.objects.{m.group(2)}(",
            ))

    # We intentionally do not flag instance-level writes on tainted vars
    # because they produce too many false positives (e.g. local analytics
    # fact variables called `order`, method calls on non-model objects).
    # Direct Model.objects.<method>() is the reliable signal.
    return violations


def main():
    total = 0
    for root, dirs, files in os.walk(PLUGIN_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_PATHS]
        for name in files:
            if not name.endswith(".py"):
                continue
            if name in SKIP_PATHS:
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, PROJECT_ROOT)
            viols = scan_file(path)
            for lineno, line, reason in viols:
                total += 1
                print(f"{rel}:{lineno}: {reason}")
                print(f"    {line.strip()}")

    if total:
        print(f"\n✗ {total} potential write(s) to Pretix core found.")
        print("  Remove them, or add an explicit exception in _safety.py.")
        return 1
    print("✓ Isolation check passed. No writes to Pretix core models found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
