# Changelog

All user-visible changes to this project are documented here. Dates are in
ISO 8601. The project follows [Semantic Versioning](https://semver.org/).

## [1.2.0] — 2026-04-24

A correctness, safety, and polish release. No breaking schema changes;
existing installations upgrade by updating the package and running
`python -m pretix migrate` (no-op if migrations are already applied).

### Safety — read-only against the Pretix ticketing database

- Added `scripts/check_isolation.py`, a standalone static check that
  greps the plugin source for any call that would write, update, or
  delete a Pretix core table (Order, OrderPosition, OrderPayment,
  LogEntry, Event, Item, Question, Checkin, …). Runs with no database,
  suitable for CI.
- Added `pretix_event_analytics/_safety.py` as the single source of
  truth for the read-only contract.
- Removed the synthetic `LogEntry` rows that `generate_test_data`
  previously inserted into the Pretix audit log for secondary-market
  testing. The plugin now writes to its own tables only.
- `generate_test_data` now refuses to run with `DEBUG=False` unless
  `--i-understand-this-is-fake-data` is passed. `--clear` prompts for
  explicit confirmation when real analytics rows would be deleted.

### Correctness

- `PRETIX_ANALYTICS_SECRET_SALT` is now required in production and
  validated to be at least 16 characters. Missing / too-short salt
  raises `ImproperlyConfigured` on first use. In `DEBUG=True`
  environments, a `SECRET_KEY`-derived fallback is used with a clear
  one-time log warning.
- `generate_repeat_hash("")` now returns an empty string. Previously,
  every order without an email collided on the HMAC of the empty
  string and was wrongly flagged as a repeat buyer of every other
  email-less order.
- Resync and live ingestion no longer race. A shared cache lock is
  published while a resync is rebuilding facts; `order_paid`,
  `order_canceled`, and `checkin_created` Celery tasks detect the
  lock and requeue themselves until the rebuild finishes.
- Per-order repeat detection is now wrapped in `transaction.atomic()`
  inside the resync path, matching the live-ingestion path.
- `process_order_paid` distinguishes expected `ValueError` (bad data,
  skip) from unexpected exceptions (bug, retry with backoff) so that
  a single upstream schema surprise no longer silently drops every
  paid order.
- `process_checkin_created` now requeues with backoff when the
  corresponding `AnalyticsOrderFact` is not yet created, instead of
  silently dropping the check-in.
- Unicode normalization (NFKC) applied before hashing so that
  visually-identical emails with different Unicode representations
  hash identically.
- Van-length buckets (`<6m`, `6–8m`, `>8m`) have documented inclusive
  / exclusive boundaries and reject negative input lengths.
- Normalizer rejects orders with zero positions (previously stored a
  degenerate fact row with `ticket_count=0`).

### Security

- Export filenames are now strictly `[A-Za-z0-9_-]+`, truncated to 50
  chars; null bytes, path separators, and traversal sequences are
  dropped.
- `TriggerResyncView` adds a per-user 60-second throttle in addition
  to the existing per-event in-progress lock, so a refresh-loop or
  scripted client can no longer flood the Celery queue.
- `DashboardFilterForm.editions` is now scoped to the current event's
  organizer on both `choices` population and `clean()`, so posting
  event ids from another organizer has no effect.

### Performance

- Dashboard refund stats folded into a single aggregate query (was
  two `.count()` calls against the same unfiltered queryset).
- Add-on attach-rate segmentation rewritten: previously one subquery
  per age bucket (N+1 against `AnalyticsTicketFact`); now a single
  `values("age_range").annotate(...)` with pre-computed add-on set
  membership. Payload reduces from 1 + 2 + 6 queries down to 3.

### UI / UX

- Cohort retention heatmap has a visible colour legend and
  distinguishes "no data" cells from "source year is later" cells.
- Dashboard shows active-filter chips below the filter bar so the
  currently applied filters are always visible.
- Date range filter (`From` / `To`) now validates that the start is
  on or before the end.
- PDF export is fully `{% trans %}`-wrapped and now includes the
  event date in the header, a cohort colour legend, and pluralised
  buyer counts.
- `pretix_event_analytics/locale/` skeleton directory added so
  translators can be on-boarded via `makemessages` / Transifex /
  Crowdin.

### Internal

- Age-confirmation keyword list consolidated in `age_bucketer.py`
  (was duplicated between `age_bucketer.py` and `normalizer.py`).
- Module docstrings in `tasks.py` and `resync_service.py` now state
  the read-only invariant explicitly.

## [1.1.1] — 2026-04-08

- Consolidated dashboard count queries into a single aggregate
  (6 queries → 1).
- Added `acks_late` to all Celery tasks to prevent message loss if a
  worker crashes mid-task.

## [1.1.0] — 2026-04-08

Initial public release.

- HMAC-SHA256 identity resolution across email, Stripe card
  fingerprints, PayPal payer IDs, bank IBANs, and name + DOB
  composites.
- Forward-looking cohort retention heatmap.
- Deterministic repeat-probability scoring (0–100), refined
  post-event via check-in data.
- Revenue KPIs, geographic and demographic breakdowns, sales pacing
  chart, buyer-persona segmentation, add-on attach rates, caravan
  analysis, and secondary-market (resale) detection.
- Multi-edition filtering, CSV and PDF export.
- GDPR data shredder integrated with Pretix's deletion workflow.
- Asynchronous Celery ingestion with retry + exponential backoff.
