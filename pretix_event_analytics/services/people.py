"""
Series-wide identity resolution ("people").

Repeat detection used to be decided once, when an order was ingested, by
looking *backwards* at whatever was already in the fact table. That made the
answer depend on the order in which editions were synced, ignored attendee
e-mails, and disagreed with the cohort matrix (which only used the buyer
e-mail).

This module resolves identities for a whole series in one pass:

1. Every buyer (order fact), every admission ticket (ticket fact) and every
   entry of an imported legacy list is an *entity*.
2. Each identity signal (``type:hash``) is a node; entities are linked to the
   signals they carry. A ticket held by the buyer is linked to its order.
3. Connected components are people. ``person_key`` is a stable hash of the
   smallest signal in the component, so it survives re-runs.
4. A person *participated* in an edition when they bought a paid,
   non-refunded order or held a ticket in one.

From that, every fact gets exact repeat fields regardless of sync order, and
the loyalty dashboards read attendance sets straight from the stored keys.

READ-ONLY AGAINST PRETIX CORE: only plugin tables are written.
"""
import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

BATCH = 1000
DEBOUNCE_SECONDS = 120
PAYMENT_SIGNALS = ("stripe_card", "paypal_payer", "bank_iban")
MAX_ORDERS_PER_PAYMENT_SIGNAL = 4


# ── Editions ──────────────────────────────────────────────────────────────────

@dataclass
class Edition:
    key: str                      # "e<event_id>" or "l<legacy_id>"
    year: int
    label: str
    active: bool
    event_id: Optional[int] = None
    legacy_id: Optional[int] = None
    date_from: object = None
    rank: int = 0

    @property
    def is_legacy(self) -> bool:
        return self.legacy_id is not None


def series_editions(organizer_id: int, series_slug: str) -> List[Edition]:
    """All editions of a series (Pretix events + legacy lists), oldest first."""
    from django_scopes import scopes_disabled

    from ..models import EventAnalyticsConfig, LegacyEdition

    editions: List[Edition] = []
    with scopes_disabled():
        configs = (
            EventAnalyticsConfig.objects.filter(
                series__organizer_id=organizer_id, series__slug=series_slug,
            )
            .select_related("event")
        )
        for c in configs:
            editions.append(Edition(
                key=f"e{c.event_id}", year=c.edition_year, label=str(c.event.name),
                active=c.is_active, event_id=c.event_id, date_from=c.event.date_from,
            ))
    for le in LegacyEdition.objects.filter(series__organizer_id=organizer_id, series__slug=series_slug):
        editions.append(Edition(
            key=f"l{le.pk}", year=le.edition_year, label=le.label, active=le.is_active, legacy_id=le.pk,
        ))
    # Legacy lists sort before a Pretix edition of the same year.
    editions.sort(key=lambda e: (
        e.year, 1 if e.event_id else 0,
        e.date_from.timestamp() if e.date_from else 0, e.event_id or e.legacy_id or 0,
    ))
    for i, e in enumerate(editions):
        e.rank = i
    return editions


# ── Union-find ────────────────────────────────────────────────────────────────

class _DSU:
    def __init__(self):
        self.parent: Dict[str, str] = {}

    def find(self, x: str) -> str:
        parent = self.parent
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Deterministic: the lexicographically smaller root wins.
            if rb < ra:
                ra, rb = rb, ra
            self.parent[rb] = ra


def _person_key(signal: str) -> str:
    return hashlib.sha256(f"person:{signal}".encode()).hexdigest()


@dataclass
class Resolution:
    editions: List[Edition]
    order_person: Dict[int, str] = field(default_factory=dict)
    ticket_person: Dict[int, str] = field(default_factory=dict)
    legacy_person: Dict[int, str] = field(default_factory=dict)
    # person_key → set of edition ranks participated in (people / buyers only)
    person_ranks: Dict[str, Set[int]] = field(default_factory=lambda: defaultdict(set))
    buyer_ranks: Dict[str, Set[int]] = field(default_factory=lambda: defaultdict(set))
    ticket_rows: list = field(default_factory=list)
    order_rows: list = field(default_factory=list)
    order_counts: Dict[int, bool] = field(default_factory=dict)


def resolve(organizer_id: int, series_slug: str, event_ids: Optional[List[int]] = None) -> Resolution:
    """
    Build person keys and participation for a series (or, when
    ``series_slug`` is empty, for the given standalone events).
    """
    from ..models import AnalyticsIdentity, AnalyticsOrderFact, AnalyticsTicketFact, LegacyIdentity

    if series_slug:
        editions = series_editions(organizer_id, series_slug)
    else:
        editions = [Edition(key=f"e{eid}", year=0, label="", active=True, event_id=eid) for eid in (event_ids or [])]
    res = Resolution(editions=editions)
    rank_by_event = {e.event_id: e.rank for e in editions if e.event_id}
    active_rank = {e.rank for e in editions if e.active}
    if not rank_by_event and not any(e.is_legacy for e in editions):
        return res

    dsu = _DSU()
    ev_ids = list(rank_by_event)

    res.order_rows = list(
        AnalyticsOrderFact.objects.filter(event_id__in=ev_ids).values_list(
            "id", "event_id", "order_status", "is_refunded",
        )
    )
    # Orders and tickets are read in separate queries; live ingestion may
    # commit in between. Tickets whose order was not loaded are skipped
    # (the next debounced run picks them up) rather than crashing the run.
    loaded_orders = {oid for oid, _e, _s, _r in res.order_rows}
    res.ticket_rows = [
        row for row in AnalyticsTicketFact.objects.filter(event_id__in=ev_ids, is_addon=False).values_list(
            "id", "order_fact_id", "attendee_is_buyer", "event_id",
        )
        if row[1] in loaded_orders
    ]
    for oid in loaded_orders:
        dsu.find(f"o{oid}")
    for tid, oid, is_buyer, _ev in res.ticket_rows:
        dsu.find(f"t{tid}")
        if is_buyer:
            dsu.union(f"t{tid}", f"o{oid}")

    identity_rows = [
        r for r in AnalyticsIdentity.objects.filter(event_id__in=ev_ids).values_list(
            "order_fact_id", "ticket_fact_id", "identity_type", "identity_hash", "event_id",
        ).iterator(chunk_size=5000)
        if r[3] and r[0] in loaded_orders
    ]
    # A payment fingerprint used by many orders within one edition is a
    # shared account (agency, company IBAN, box office, a family card), not
    # a person. Linking through it would merge strangers transitively.
    per_edition = defaultdict(set)
    for oid, _tid, itype, ihash, ev in identity_rows:
        if itype in PAYMENT_SIGNALS:
            per_edition[(itype, ihash, ev)].add(oid)
    shared = {(t, h) for (t, h, _ev), orders in per_edition.items() if len(orders) > MAX_ORDERS_PER_PAYMENT_SIGNAL}

    has_signal: Set[str] = set()
    for oid, tid, itype, ihash, _ev in identity_rows:
        if (itype, ihash) in shared:
            continue
        entity = f"t{tid}" if tid else f"o{oid}"
        signal = f"#{itype}:{ihash}"
        dsu.union(entity, signal)
        has_signal.add(signal)

    legacy_rank = {e.legacy_id: e.rank for e in editions if e.is_legacy}
    legacy_entities = []
    if legacy_rank:
        for lid, le_id, itype, ihash in LegacyIdentity.objects.filter(
            legacy_edition_id__in=list(legacy_rank)
        ).values_list("id", "legacy_edition_id", "identity_type", "identity_hash").iterator(chunk_size=5000):
            signal = f"#{itype}:{ihash}"
            dsu.union(f"l{lid}", signal)
            has_signal.add(signal)
            legacy_entities.append((f"l{lid}", legacy_rank[le_id]))

    # Components are rooted at their smallest node. Signals start with "#"
    # and entities with "l"/"o"/"t", so an identified component's root is
    # always a signal — which makes person keys stable across runs.
    def key_for(entity: str) -> str:
        root = dsu.find(entity)
        return _person_key(root) if root in has_signal else ""

    order_counts = {}
    for oid, ev, status, refunded in res.order_rows:
        k = key_for(f"o{oid}")
        res.order_person[oid] = k
        counts = status == "p" and not refunded
        order_counts[oid] = counts
        res.order_counts[oid] = counts
        rank = rank_by_event[ev]
        if k and counts and rank in active_rank:
            res.person_ranks[k].add(rank)
            res.buyer_ranks[k].add(rank)
    for tid, oid, _b, ev in res.ticket_rows:
        k = key_for(f"t{tid}")
        res.ticket_person[tid] = k
        rank = rank_by_event[ev]
        if k and order_counts.get(oid) and rank in active_rank:
            res.person_ranks[k].add(rank)
    for entity, rank in legacy_entities:
        k = key_for(entity)
        res.legacy_person[int(entity[1:])] = k
        if k and rank in active_rank:
            res.person_ranks[k].add(rank)
            res.buyer_ranks[k].add(rank)
    return res


def _previous_active_rank(editions: List[Edition], rank: int) -> Optional[int]:
    for e in reversed(editions[:rank]):
        if e.active:
            return e.rank
    return None


def _repeat_fields(ranks: Set[int], own_rank: int, editions: List[Edition], counts: bool = True) -> dict:
    prev = sorted(r for r in ranks if r < own_rank)
    last = _previous_active_rank(editions, own_rank)
    # The own edition is part of the tally only when this order/ticket
    # itself counts (paid, not refunded, edition active) — same rule as the
    # attendance sets, so the column and the dashboards agree.
    own = {own_rank} if counts and editions[own_rank].active else set()
    return {
        "count": len(prev),
        "first_year": editions[prev[0]].year if prev else None,
        "from_last": last is not None and last in ranks,
        "attended": max(1, len(ranks | own)),
    }


# ── Recompute (writes) ────────────────────────────────────────────────────────

def recompute_series(organizer_id: int, series_slug: str) -> dict:
    """Resolve people for a series and rewrite every derived fact field."""
    return _recompute(organizer_id, series_slug, None)


def recompute_event(event) -> dict:
    """Standalone event (no series): person keys only, nobody is 'returning'."""
    return _recompute(event.organizer_id, "", [event.pk])


def recompute_for_event(event) -> dict:
    from ..models import EventAnalyticsConfig

    cfg = EventAnalyticsConfig.objects.select_related("series").filter(event=event).first()
    if cfg and cfg.series:
        return recompute_series(event.organizer_id, cfg.series.slug)
    return recompute_event(event)


def _recompute(organizer_id: int, series_slug: str, event_ids: Optional[List[int]]) -> dict:
    from ..models import AnalyticsOrderFact, AnalyticsTicketFact
    from .predictor import score_from_fact
    from .versioning import bump

    res = resolve(organizer_id, series_slug, event_ids)
    editions = res.editions
    rank_by_event = {e.event_id: e for e in editions if e.event_id}
    if not rank_by_event:
        _write_legacy_keys(res)
        bump(organizer_id)
        return {"orders": 0, "tickets": 0}

    # Keep denormalised series/edition columns in step with the config
    # (the config may have been edited after the facts were written).
    if series_slug:
        for e in editions:
            if e.event_id:
                AnalyticsOrderFact.objects.filter(event_id=e.event_id).exclude(
                    series_slug=series_slug, edition_year=e.year,
                ).update(series_slug=series_slug, edition_year=e.year)
    else:
        AnalyticsOrderFact.objects.filter(event_id__in=list(rank_by_event)).exclude(
            series_slug="",
        ).update(series_slug="")

    from ..models import EventAnalyticsConfig

    home_country = dict(
        EventAnalyticsConfig.objects.filter(event_id__in=list(rank_by_event)).values_list("event_id", "home_country")
    )

    changed_orders = 0
    order_fields = [
        "is_local_buyer", "person_key", "is_repeat_buyer", "repeat_from_any_previous", "repeat_from_last_edition",
        "repeat_count", "first_seen_edition_year", "editions_attended", "predicted_repeat_probability",
    ]
    qs = AnalyticsOrderFact.objects.filter(event_id__in=list(rank_by_event)).only(
        "id", "event_id", "country_code", "ticket_count", "is_group_order", "days_before_event",
        "checkin_completed", *order_fields,
    )
    pending = []
    for fact in qs.iterator(chunk_size=BATCH):
        if fact.pk not in res.order_person:
            continue  # arrived after the resolver read the table; next run
        k = res.order_person[fact.pk]
        own = rank_by_event[fact.event_id].rank
        rf = _repeat_fields(res.person_ranks.get(k, set()) if k else set(), own, editions,
                            counts=res.order_counts.get(fact.pk, False))
        home = (home_country.get(fact.event_id) or "").upper()
        new = {
            "is_local_buyer": bool(home) and fact.country_code == home,
            "person_key": k,
            "is_repeat_buyer": rf["count"] > 0,
            "repeat_from_any_previous": rf["count"] > 0,
            "repeat_from_last_edition": rf["from_last"],
            "repeat_count": rf["count"],
            "first_seen_edition_year": rf["first_year"],
            "editions_attended": rf["attended"],
        }
        before = tuple(getattr(fact, f) for f in order_fields)
        for f, v in new.items():
            setattr(fact, f, v)
        fact.predicted_repeat_probability = score_from_fact(fact)
        if tuple(getattr(fact, f) for f in order_fields) != before:
            pending.append(fact)
        if len(pending) >= BATCH:
            AnalyticsOrderFact.objects.bulk_update(pending, order_fields)
            changed_orders += len(pending)
            pending = []
    if pending:
        AnalyticsOrderFact.objects.bulk_update(pending, order_fields)
        changed_orders += len(pending)

    ticket_fields = [
        "attendee_person_key", "is_returning_attendee", "attendee_previous_editions",
        "attendee_first_seen_year", "attendee_editions_attended",
    ]
    tq = AnalyticsTicketFact.objects.filter(event_id__in=list(rank_by_event), is_addon=False).only(
        "id", "event_id", "order_fact_id", *ticket_fields,
    )
    pending = []
    changed_tickets = 0
    for t in tq.iterator(chunk_size=BATCH):
        if t.pk not in res.ticket_person:
            continue
        k = res.ticket_person[t.pk]
        own = rank_by_event[t.event_id].rank
        rf = _repeat_fields(res.person_ranks.get(k, set()) if k else set(), own, editions,
                            counts=res.order_counts.get(t.order_fact_id, False))
        before = tuple(getattr(t, f) for f in ticket_fields)
        t.attendee_person_key = k
        t.is_returning_attendee = rf["count"] > 0
        t.attendee_previous_editions = rf["count"]
        t.attendee_first_seen_year = rf["first_year"]
        t.attendee_editions_attended = rf["attended"]
        if tuple(getattr(t, f) for f in ticket_fields) != before:
            pending.append(t)
        if len(pending) >= BATCH:
            AnalyticsTicketFact.objects.bulk_update(pending, ticket_fields)
            changed_tickets += len(pending)
            pending = []
    if pending:
        AnalyticsTicketFact.objects.bulk_update(pending, ticket_fields)
        changed_tickets += len(pending)

    _write_legacy_keys(res)
    bump(organizer_id)
    logger.info("analytics: resolved people for %s/%s — %d orders, %d tickets",
                organizer_id, series_slug or event_ids, changed_orders, changed_tickets)
    return {"orders": changed_orders, "tickets": changed_tickets}


def _write_legacy_keys(res: "Resolution") -> None:
    if not res.legacy_person:
        return
    from ..models import LegacyIdentity

    rows = list(LegacyIdentity.objects.filter(pk__in=list(res.legacy_person)).only("id", "person_key"))
    stale = [r for r in rows if r.person_key != res.legacy_person[r.pk]]
    for r in stale:
        r.person_key = res.legacy_person[r.pk]
    LegacyIdentity.objects.bulk_update(stale, ["person_key"], batch_size=BATCH)


def scope_of(event):
    """(organizer_id, series_slug or '', event_id or None) — what a recompute covers."""
    from ..models import EventAnalyticsConfig

    cfg = EventAnalyticsConfig.objects.select_related("series").filter(event=event).first()
    if cfg and cfg.series:
        return event.organizer_id, cfg.series.slug, None
    return event.organizer_id, "", event.pk


def _scope_token(organizer_id, series_slug, event_id) -> str:
    return f"{organizer_id}:{series_slug or ''}:{event_id or ''}"


def pending_key(organizer_id, series_slug, event_id=None) -> str:
    return f"pretix_analytics:people_pending:{_scope_token(organizer_id, series_slug, event_id)}"


def schedule_recompute(event) -> None:
    """
    Ask for a series recompute after live ingestion.

    With a Celery worker, one debounced task per *series* is queued, so a
    sale opening collapses into a single resolver run. Without a worker
    (Celery would run inline, inside the buyer's payment request) nothing is
    done here: the periodic task (Pretix cron) notices the new facts and
    resolves them. Until then, orders carry the immediate backward-looking
    repeat status written at ingestion.
    """
    from django.conf import settings
    from django.core.cache import cache

    if not getattr(settings, "HAS_CELERY", False):
        return

    from ..tasks import recompute_people_scope

    org, slug, event_id = scope_of(event)
    try:
        # Timeout well above the countdown: a backlogged worker must not let
        # the key lapse and queue duplicates. The task clears it when it starts.
        if not cache.add(pending_key(org, slug, event_id), True, timeout=DEBOUNCE_SECONDS * 10):
            return
    except Exception:
        logger.debug("analytics: debounce cache unavailable", exc_info=True)
    recompute_people_scope.apply_async(args=[org, slug, event_id], countdown=DEBOUNCE_SECONDS)


def queue_recompute(organizer_id: int, series_slug: str, event_id: Optional[int] = None) -> None:
    """Background recompute for admin actions (never block a web request on it)."""
    from ..tasks import recompute_people_scope

    recompute_people_scope.apply_async(args=[organizer_id, series_slug, event_id])


def run_scope(organizer_id: int, series_slug: str, event_id: Optional[int] = None) -> dict:
    """Recompute one scope and record when it was resolved (in the database)."""
    from django.utils.timezone import now

    from ..models import EventAnalyticsConfig

    started = now()
    if series_slug:
        out = recompute_series(organizer_id, series_slug)
        configs = EventAnalyticsConfig.objects.filter(series__organizer_id=organizer_id, series__slug=series_slug)
    else:
        from django_scopes import scopes_disabled
        from pretix.base.models import Event

        with scopes_disabled():
            event = Event.objects.filter(pk=event_id).first()
        out = recompute_event(event) if event else {}
        configs = EventAnalyticsConfig.objects.filter(event_id=event_id)
    configs.update(people_resolved_at=started)
    return out


def resolve_dirty_scopes() -> int:
    """
    Periodic safety net: recompute every series (or standalone event) whose
    facts changed since it was last resolved. Idempotent and cheap when
    nothing changed (one aggregate per series).
    """
    from django.db.models import Max

    from ..models import AnalyticsOrderFact, EventAnalyticsConfig

    scopes = {}
    for cfg in EventAnalyticsConfig.objects.select_related("series", "event"):
        key = ((cfg.series.organizer_id, cfg.series.slug, None) if cfg.series
               else (cfg.event.organizer_id, "", cfg.event_id))
        scopes.setdefault(key, []).append(cfg)
    ran = 0
    for (org, slug, event_id), configs in scopes.items():
        ids = [c.event_id for c in configs]
        last_change = AnalyticsOrderFact.objects.filter(event_id__in=ids).aggregate(m=Max("updated_at"))["m"]
        if last_change is None:
            continue
        resolved = [c.people_resolved_at for c in configs]
        if all(resolved) and last_change <= min(resolved):
            continue
        run_scope(org, slug, event_id)
        ran += 1
    return ran
