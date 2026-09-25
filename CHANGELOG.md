# Changelog

All user-visible changes to this project are documented here. Dates are in
ISO 8601. The project follows [Semantic Versioning](https://semver.org/).

## [2.0.0] — 2026-09-25

A rebuild into a full analytics suite. **Upgrade steps:** update the
package, run `python -m pretix migrate`, `python -m pretix rebuild`, set the
salt in `pretix.cfg` if you used a Django setting that Pretix never
loaded, then run `python -m pretix analytics_resync --all`. Until the
resync runs, the dashboard shows a notice for rows produced by 1.x.

### New — dashboard suite
- Seven pages sharing one filter bar: Overview, Sales, Audience, Loyalty,
  Tickets, Operations, Resale; sidebar sub-navigation.
- **Sales:** tickets and revenue over time as aligned charts (day / week /
  month, cumulative toggle), split by product, category, new vs returning
  or country, by order or payment date; price-tier markers; editions
  compared on a days-before-event axis; same-point comparison table;
  forecast with range; ticket and revenue targets; weekday × hour heatmap;
  purchase-timing buckets.
- **Loyalty:** first-timer share, times attended before, came back after a
  gap, lost since the last edition, at every edition; choose any editions
  to compare — attended once/twice/…, overlap matrix, attendance patterns,
  edition flow, first-timer cohorts; people or buyers-only; first-timer
  segments by product, country, age and purchase timing.
- **Audience, Tickets, Operations:** product performance, categories,
  variations, price tiers, add-ons, vouchers, check-in and arrivals,
  no-shows, cancellations over time, opt-in question breakdowns.
- **Series overview** page and **past attendee-list import** (hashed).
- **Like-for-like KPIs** against the previous edition at the same point
  in its sales cycle.
- **Exports:** tickets CSV, "returning buyers by order code" CSV, full PDF
  report, and exporters in Pretix's own Export menu (CSV/Excel).

### Fixed — correctness
- Orders with several tickets of the same product lost tickets during live
  ingestion and were skipped entirely by resync.
- Returning status depended on the order in which editions were synced;
  it is now resolved series-wide after every change.
- Attendee e-mails were hashed but never used; a friend's ticket in one
  year and an own purchase later now match. Group orders no longer merge
  their attendees into one person.
- The cohort matrix used a different definition of "returning" than the
  KPIs; both now use the same resolved people.
- A check-in removed the early-purchase points from the return score.
- With several events selected, ticket, add-on and refund figures still
  showed only the current event.
- Resync emptied the table first (blank dashboard during a resync, partial
  data after a failure) and dropped canceled/refunded orders, so the
  refund rate read 0 after any resync.
- Daily/hourly charts used UTC instead of the event's timezone; the hourly
  chart also shifted by the viewer's browser timezone.
- Ages were computed on the day of the sync instead of the event date.
- Persona "add-on rate" actually showed the caravan rate.
- Net revenue and tax ignored fees.
- Series pages crashed on Pretix 2026 (non-existent base template).
- Cohort tooltips showed "0 of  buyers".
- The salt could only be set as a Django setting, which Pretix installs do
  not load; `pretix.cfg` and environment variables now work.

### New — also in 2.0.0
- **Win-back lists:** people from the previous edition who have not
  bought yet, and people who came earlier but skipped the last edition —
  exported as the order code of their most recent visit.
- **Moments:** dated notes ("line-up announced") drawn on the sales charts
  and on the edition comparison, next to automatic price-tier markers.
- **Capacity** per quota (paid, pending, available, waiting list), live
  from Pretix.
- **Payment completion** by method: paid vs pending vs expired unpaid.
- **Top cities** in the Audience page.
- **Quick date ranges** (7 / 30 / 90 days, all time) in the filter bar.
- **Pretix dashboard widgets:** returning buyers and first-time attendees
  on the event's main dashboard.

### Security
- The "merge editions" filter and all series-wide figures (Loyalty,
  first-timer headline, edition comparisons, resale by edition, series
  overview) respect per-event team permissions: a member limited to one
  event can no longer read other editions' data.
- CSV exports neutralise spreadsheet formulas in buyer-supplied fields.

### Compatibility
- Tested on Pretix 2026.2.0 and 2026.3.1. Uses the new permission names
  (`event.orders:read`, …) on 2026.3+ and the legacy ones before.
- Declared as an event + organizer plugin: **enable it in the organizer's
  plugin settings as well as per event.**
- All markup is compatible with Pretix's Content-Security-Policy
  (`style-src 'self'`): no inline style attributes; 1.x's inline styles
  were silently ignored by browsers.

### Changed
- Resync upserts instead of delete-and-rebuild, always includes check-ins,
  keeps its lock alive per chunk, and resolves returning people once per
  series. Live orders wait for a running resync instead of being dropped.
- Returning-people resolution runs as one debounced background task per
  series (with a Celery worker) or from Pretix's periodic cron task
  (without one) — never inside a buyer's payment request. Admin actions
  queue it instead of blocking the page.
- Payment fingerprints shared by more than four orders in one edition
  (agency, company account, box office) no longer link people together.
- Order changes, attendee edits, reactivations and splits are re-ingested.
- Report results are cached per organizer data version and plugin version.
- Charts: validated colour-blind-safe palette, no dual axes, table view.

### Development
- pytest suite (~80 tests) against real Pretix objects.
- `scripts/dev_seed_orders.py` seeds real orders into a local dev DB.

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
