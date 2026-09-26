# Changelog

All user-visible changes to this project are documented here. Dates are in
ISO 8601. The project follows [Semantic Versioning](https://semver.org/).

## [2.2.0] — 2026-09-26

**Run a resync of every edition after upgrading** (data version 4). One database
migration (0010, fields added only, reversible).

### Added
- **Nationality, kept apart from residence.** Per ticket holder, from a
  nationality question (country names *and* demonyms such as "Portuguese") or
  from an ID document that proves citizenship. New "Nationality" panel on the
  Audience page (replaces "ID-document country"), and `nationality` columns in
  the order and ticket CSV exports.
- ID-document rules now declare what they prove (`id_country.PROVES`): citizens'
  IDs (Spanish DNI, Portuguese Cartão de Cidadão / civil number, Italian codice
  fiscale of someone born in Italy) prove **nationality**; the Spanish NIE (issued
  to foreigners), UK driving licence and Belgian national number / eID prove
  **residence**.

### Changed
- **Exact residence = what the buyer stated** (residence question, invoice,
  PayPal, card billing address) or a residence document. The paying bank's
  country (card issuer, IBAN) and the buyer's nationality are now **probable**
  residence only. Measured on SUTI 2026, the card issuer agreed with the ID
  document 79% and the invoice address 86% of the time, and foreign-issued
  cards (Israel, UK, …) mostly belonged to people with Portuguese documents.
- Derived-country order: same customer's other orders (inferred) → buyer's
  nationality → card issuer → IBAN → e-mail domain (all probable).
- "Same customer's other orders" only uses exact countries as evidence, so a
  bank's country never spreads to other orders.
- "Local buyer" now needs an exact residence country.

### Fixed
- Placeholder ID numbers made of one repeated digit ("00000000") no longer
  resolve to a country.

## [2.1.1] — 2026-09-25

### Fixed
- **Resale showed 0 TicketSwap resales** with TicketSwap 1.x, which keeps
  swaps in its own table (`TicketSwapSwap`, successful rows) instead of on
  the ticket. Both record shapes are now read (1.x table, 2.x ticket
  counters), read-only — only positions, dates and the success flag, never
  the stored names or e-mails.
- TicketSwap swap dates (1.x) now appear on the monthly chart, stacked with
  manual name changes.
- Resale counts **admission tickets only** in both channels and in the
  total, so add-ons and stand-alone extras (parking, camper passes) no longer
  skew the rate.

No migration. No resync needed (Resale reads Pretix data live).

## [2.1.0] — 2026-09-25

**Run a resync of every edition after upgrading** — returning-people figures
change meaning (see below). One database migration (fields added, one
constraint widened).

### Changed
- **Returning people are matched on name + birth date only.** A person is a
  ticket holder. Sharing a card, a PayPal account or an e-mail no longer makes
  two ticket holders the same person (people buy for friends). Names are
  compared ignoring accents, case, word order, middle names and added second
  surnames; different birth dates never match.
- **When in doubt, unknown.** Tickets without a usable name + birth date,
  ambiguous names and placeholder data (the same identity on 3+ tickets of one
  edition) are left out of returning-people figures instead of being guessed.
  A conservative *probable* tier (no birth date, one matching person, shared
  e-mail or card) is shown only as "including probable matches".
- **Customers** (returning buyers) are matched on the order e-mail only;
  payment fingerprints are no longer used to link buyers.
- **Country of residence** comes from the first available source, stored with
  it: residence question → invoice address → PayPal address/account → card
  billing address → ID-document country (opt-in) → IBAN → card's issuing
  bank. Invoice addresses now beat card countries. Exact country names only
  (no fuzzy matching).
- **Resale** shows one "changed hands" total: TicketSwap resales (read-only,
  from the TicketSwap plugin's ticket data) plus manual name changes, counted
  once per ticket, with a channel breakdown, by product, current holders and a
  CSV of order codes. A manual name change counts only with evidence (birth
  date or attendee e-mail changed too, or a clearly different second rename);
  other single edits are reported as "unclear".

### Added
- **Derived countries, kept apart from exact ones.** For orders with no exact
  source: *inferred* — the same customer's country on their other orders
  (same order e-mail, or the buyer holding a ticket as the same person),
  only when all of them agree; *probable* — the e-mail's country domain
  (.pt, .es, .uk …; generic and vanity domains ignored). Stored in separate
  fields, never in the country used by filters and other pages. Audience has
  an **Exact / + inferred / + probable** switch; orders CSV has the columns.
- PayPal v2 order data: country from `payer.address` and
  `payment_source.paypal.address` (pretix 2025+ stores v2).
- "Travelling from" question support and a breakdown on the Audience page,
  plus "How the country is known" coverage by source.
- Opt-in ID-document country (settings → "Where people come from"): issuing
  country from national ID formats with a valid check digit; the number is
  never stored.
- Past-edition imports accept a CSV with name + birth-date columns.
- Tickets CSV: match tier, returning incl. probable, document country.

### Fixed
- Dashboard widgets showed raw HTML on Pretix 2026.7 (content is now marked safe).
- Two tickets of one order carrying the same signal (e.g. the same attendee
  e-mail) kept it only on the first ticket.
- Win-back list and loyalty CSV mixed customer and person keys.

## [2.0.3] — 2026-09-25

### Fixed
- Stripe card details were read from the wrong place. Pretix stores Stripe
  payments as PaymentIntents (card under `charges.data[].payment_method_details`),
  so card country and card fingerprint were never found: buyers without an
  invoice address showed as "Unknown" country, and returning buyers who
  changed e-mail but paid with the same card were not recognised. Now reads
  PaymentIntent, `latest_charge`, Charge and legacy card-source shapes; also
  accepts the `stripe_cc` / `paypal2` provider identifiers.
  **Run a resync after upgrading** so existing orders pick this up.

## [2.0.2] — 2026-09-25

### Changed
- New `--pa-accent` variable for UI states (active tab, selected segment,
  quick-range pill, callout border, toggles, attendance dots). It defaults to
  the first chart colour, so nothing changes visually, but a theme can now
  give the UI its brand accent without recolouring the chart series.

## [2.0.1] — 2026-09-25

### Fixed
- Themed (e.g. dark) control panels: every colour is now a CSS variable and
  every surface sets its own text colour, while text placed directly on the
  page follows the host theme. Previously the settings page used fixed white
  cards, so headings and labels became invisible under a dark theme with
  light text, and some dashboard text was drawn dark on a dark page.
- The settings and series pages share the dashboard's variable scope, so a
  theme that remaps `--pa-*` variables styles them too.
- Chart tooltips take their colours from variables.

### Changed
- Plugin author shown in Pretix is now "Hexadexa".

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
- **Pace alerts:** e-mail chosen addresses when ticket sales trail the
  previous edition (same number of days before the event) by a chosen
  percentage; at most once a week while behind. Runs from Pretix's cron.
- **Calmer settings and filters:** the settings page is grouped into
  *This edition* (required) and collapsible *Goals & alerts*, *Question
  insights* and *Advanced* sections, each with a one-line summary; the
  dashboard filter bar keeps the common filters visible and folds the rest
  under *More filters* (opens automatically when one is in use); long pages
  get an "On this page" jump bar.

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
