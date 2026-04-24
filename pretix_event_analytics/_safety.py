"""
Isolation invariant — machine-readable contract.

This plugin is a READ-ONLY overlay over Pretix core. It must never write,
update, or delete rows in the Pretix ticketing tables. All persistence
happens in the plugin's own models (AnalyticsOrderFact, AnalyticsTicketFact,
AnalyticsIdentity, EventSeries, EventAnalyticsConfig).

If a future feature genuinely needs to write something to the Pretix core
tables, it must be added to ALLOWED_WRITE_POINTS with a code review note
explaining why, and any such write must be gated behind an explicit
context manager so the isolation check remains useful.

The companion ``scripts/check_isolation.py`` script greps the plugin source
for any write-style call targeting the models below and fails if it finds
one that is not listed in ALLOWED_WRITE_POINTS.
"""

# Pretix core models the plugin legitimately reads from. The checker uses
# this list as the set of names to watch for write calls against.
PRETIX_CORE_READ_MODELS = frozenset({
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
})

# Write calls we refuse to see in plugin source, as substrings. The checker
# flags any match of <CoreModel>.objects.<method>(.
FORBIDDEN_WRITE_METHODS = (
    ".save(",
    ".delete(",
    ".update(",
    ".create(",
    ".update_or_create(",
    ".get_or_create(",
    ".bulk_create(",
    ".bulk_update(",
    ".raw(",
)

# Explicit, reviewed escape hatches. Empty by design — every entry is a
# conscious exception. Format: (<relative path>, <line number or marker>).
ALLOWED_WRITE_POINTS = frozenset()
