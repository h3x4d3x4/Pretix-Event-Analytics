# Pretix Event Analytics

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Pretix](https://img.shields.io/badge/Pretix-2025.x%2B-purple.svg)](https://pretix.eu) ![Version](https://img.shields.io/badge/version-2.0.3-green.svg)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://python.org)

Analytics suite for [Pretix](https://pretix.eu): sales over time and against previous editions, first-timers and returning people across every edition of a recurring event, audience, tickets, check-in and refunds — without storing personal data.

## Features

**Seven dashboard pages** (Overview · Sales · Audience · Loyalty · Tickets · Operations · Resale) share one filter bar: order date range, country, age, first-time vs returning buyers, payment method, products, merged editions and canceled/refunded orders.

- **Overview** — revenue, tickets, orders, average order, returning buyers, refunds and check-in, each compared like-for-like with the previous edition *at the same number of days before the event*; first-timer headline; sales pace of every edition; forecast.
- **Sales** — tickets sold **and** revenue as two aligned charts (day / week / month, daily or cumulative), split by product, category, new vs returning or country, by order or payment date. Price-tier changes and your own **moments** ("line-up announced") are marked on the timeline and on the edition comparison. Editions compared on a days-before-event axis with a same-point table and a forecast (with range and optional ticket/revenue targets). Weekday × hour heatmap and how far ahead people buy.
- **Loyalty** — **win-back lists** (people from the last edition not back yet, and lapsed visitors, as order codes), the share of first-timers, how many times people have been before, who came back after a gap, who from the last edition has not (yet) returned, who has been to every edition. Pick **any set of editions** to see how many people attended once, twice, three times…, an overlap matrix, the most common attendance patterns, edition-by-edition flow and whether first-timers come back. Counts *people* (buyers and ticket holders) or buyers only. Segments show who the first-timers are (product, country, age, purchase timing).
- **Audience** — countries and top cities (orders, revenue, average order, returning share), age on the event date, checkout language, payment methods, buyer personas (early bird / regular / last minute), group size, caravans.
- **Tickets** — capacity per quota, product performance (returning share, check-in rate), categories, variations, price tiers, add-on attach rates and who buys them, voucher and discount-code usage.
- **Operations** — payment completion by method (paid vs expired unpaid), check-in rate, no-shows, arrival curve, check-in by product and by first-time/returning; cancellations and refunds over time and by product; opt-in breakdowns of yes/no and multiple-choice questions (e.g. "How did you hear about us?") split by first-time and returning attendees.
- **Pretix dashboard widgets** — returning buyers and first-time attendees on each event's main dashboard.
- **Resale** — attendee-name changes as a secondary-market signal, per month and per edition.
- **Series overview** (organizer level) — every edition side by side, edition flow and first-timer cohorts, plus **import of past attendee lists** (e.g. a 2019 mailing list) so people from before Pretix count as returning. Only hashes of the addresses are kept.
- **Exports** — orders, tickets and "returning buyers by order code" as CSV (respecting filters), a full PDF report, and two exporters in Pretix's own *Export* menu (CSV/Excel).

### How returning people are detected

Every buyer and every ticket holder is linked through identity signals — order e-mail, attendee e-mail, name + date of birth, Stripe card fingerprint, PayPal payer ID, IBAN — all stored only as HMAC-SHA256 hashes. Signals are grouped per series into *people*, so:

- an attendee who got a ticket from a friend in 2024 and buys their own in 2026 is recognised;
- a group order for five friends counts as five people, not one;
- the result is the same no matter in which order editions were synced.

Tickets without any signal (group tickets with no attendee e-mail or birth date) are counted in totals and shown as a coverage figure, but never guessed.

## Requirements

| Component | Version |
|-----------|---------|
| Pretix    | 2026.2 or later (tested on 2026.2.0 and 2026.3.1) |
| Python    | 3.10, 3.11, or 3.12 |
| Django    | 4.2 LTS (ships with Pretix) |
| Redis     | Recommended (for caching and Celery task queue) |

## Installation

### From source

```bash
git clone https://github.com/h3x4d3x4/Pretix-Event-Analytics.git
cd Pretix-Event-Analytics

# Install into your Pretix virtualenv
pip install -e .

# Run database migrations
python -m pretix migrate
```

### From pip

```bash
pip install pretix-event-analytics
python -m pretix migrate
```

### Enable the plugin

The plugin works at two levels (Pretix "event + organizer" plugin):

1. **Organizer** → Settings → **Plugins** → enable **Event Analytics** (unlocks the series pages).
2. **Each event** → Settings → **Plugins** → enable **Event Analytics** (dashboards and order processing).

Orders are only processed for events where it is enabled at both levels.

The plugin is auto-discovered via its entry point &mdash; no changes to `INSTALLED_APPS` are needed.

## Quick Start

### 1. Create an Event Series

Navigate to your organizer sidebar &rarr; **Analytics Series** &rarr; **Create series**.

A series groups your recurring editions (e.g. "My Festival" containing 2022, 2023, 2024 editions).

### 2. Configure each Event

Go to **Settings &rarr; Analytics** on each event:

- Assign the event to a series
- Set the edition year
- Optionally set the event country for local-buyer scoring

### 3. Sync existing data

For events with existing orders, trigger a resync — **Resync** on the dashboard, **Resync all editions** on the series page, or from the command line:

```bash
# Every configured event (returning people are resolved once per series at the end)
python -m pretix analytics_resync --all

# One event / one series
python -m pretix analytics_resync --event <organizer>/<event>
python -m pretix analytics_resync --series <organizer>/<series-slug>

# Preview without processing
python -m pretix analytics_resync --all --dry-run
```

Check-ins are always included; the old `--checkin` flag is accepted but no longer needed.

### 4. Optional

- **Past editions:** on the series page, import attendee e-mail lists of editions that predate Pretix.
- **Questions:** in the event's Analytics settings, choose yes/no or multiple-choice questions to analyse, then resync.
- **Targets & pace alert:** under *Goals & alerts*, set a ticket and/or revenue target to see progress next to the forecast, and optionally have e-mails sent when sales fall a chosen percentage behind the previous edition.

## Architecture

```
Pretix signals (order paid / canceled / changed / modified / reactivated / split, check-in)
        │   thin handlers → Celery tasks (retry + backoff; resync lock respected)
        ▼
services/ingest.write_order ── normalizer (hashing, bucketing) ──► AnalyticsOrderFact
        │                                                         AnalyticsTicketFact (one per position)
        │                                                         AnalyticsIdentity (buyer / attendee)
        │                                                         AnalyticsAnswerFact (opt-in questions)
        ▼
services/people  (debounced, series-wide)  → person keys + exact repeat fields
        ▼
services/attendance  (cached sets per edition)   services/reports/*  (cached per data version)
        ▼
Dashboard pages · series overview · CSV / PDF / Pretix exporters
```

Resync upserts every paid or refunded order through the same `write_order` path and removes stale rows; the table is never emptied, so dashboards keep working during a resync.

## Access Control

| Permission | Access |
|------------|--------|
| `can_view_orders` | View every analytics page of an event, export CSV/PDF |
| `can_change_event_settings` | Configure analytics (series, targets, questions), trigger a resync |
| `can_change_organizer_settings` | Manage series, series overview, import past attendee lists, resync a whole series |

These are standard Pretix team permissions.

## Safety Guarantee: Read-Only Against the Ticketing Database

This plugin is an analytics overlay. It reads Pretix orders, positions,
answers, payments, refunds and check-ins — it **never writes** to any
Pretix core table. All persistence happens in the plugin's own analytics
tables (`AnalyticsOrderFact`, `AnalyticsTicketFact`, `AnalyticsIdentity`,
`AnalyticsAnswerFact`, `EventSeries`, `EventAnalyticsConfig`,
`LegacyEdition`, `LegacyIdentity`).

The invariant is enforced two ways:

1. **Static check** — [`scripts/check_isolation.py`](scripts/check_isolation.py)
   greps the plugin source for any `.save()`, `.delete()`, `.create()`,
   `.update()`, `.bulk_create()`, `.bulk_update()`, or `.raw()` call
   targeting a Pretix core model and exits non-zero if it finds one. Run
   it any time; it requires no database and no Django setup.
2. **Signal-path discipline** — signal handlers do zero DB work inline;
   they dispatch a Celery task. All task code is read-only against
   `pretix.base.models.*`. Failures in analytics never block, delay, or
   roll back a ticket checkout or payment.

## Privacy & GDPR

- Email addresses, card fingerprints, and other identity signals are
  converted to HMAC-SHA256 hashes — never stored in plain text and not
  reversible.
- Birth dates are converted to age buckets (e.g. `25-34`) — the raw date
  is never stored.
- Names are never stored in analytics tables.
- Question answers are only collected for questions an organiser opts in
  to, only for yes/no and multiple-choice questions, and only as the
  chosen option — free text never reaches the analytics tables.
- Imported past attendee lists are hashed in memory; the uploaded file
  and the plain addresses are discarded immediately.
- Exports contain order codes and pseudonymous fields only — never names
  or e-mail addresses.
- The HMAC salt is **required** in production; the plugin refuses to
  operate without it (see *Configuration*). In `DEBUG=True` development
  environments the salt falls back to a `SECRET_KEY`-derived value, with
  a clear log warning.
- A **data shredder** is registered, so organisers can delete all
  analytics data for an event through Pretix's built-in GDPR data
  export / deletion interface.

> **Important:** the salt must be set once and
> never changed. Rotating it invalidates every historical repeat-buyer
> hash — repeat detection silently starts over from scratch. If you have
> to rotate (e.g. after a security incident), run a full resync
> (`analytics_resync --all`) immediately after.

## Configuration

Set the HMAC salt once, before the first sync, in `pretix.cfg`:

```ini
[pretix_event_analytics]
; 32+ random characters. Never change it once data exists.
secret_salt = your-long-random-string-here-32-chars-minimum
```

Alternatives: the environment variable `PRETIX_ANALYTICS_SECRET_SALT`, or a Django setting of the same name in a custom settings module. Without a salt the plugin refuses to process orders in production (`DEBUG=False`).

After upgrading, run `python -m pretix migrate`, `python -m pretix rebuild` (static files) and a resync of all events (`analytics_resync --all`) so existing data gets the new fields.

## Data Model

```
EventSeries (organizer)
  ├─ EventAnalyticsConfig (per event: edition year, home country, active, targets, tracked questions)
  │    └─ AnalyticsOrderFact (per order; all dashboards query facts only)
  │         ├─ AnalyticsTicketFact (per position: product, price, voucher, check-in, attendee status)
  │         │    ├─ AnalyticsIdentity (attendee-level hashes)
  │         │    └─ AnalyticsAnswerFact (opt-in question answers)
  │         └─ AnalyticsIdentity (buyer-level hashes)
  └─ LegacyEdition (imported past edition)
       └─ LegacyIdentity (hashed e-mails)
```

## Development

```bash
.venv/bin/pip install pytest pytest-django
.venv/bin/python -m pytest            # real Pretix objects, ~80 tests

# Local demo data: real orders for six editions (local SQLite dev DB only)
PRETIX_CONFIG_FILE=pretix.cfg .venv/bin/python scripts/dev_seed_orders.py --organizer <org> --i-understand-this-writes-orders
PRETIX_CONFIG_FILE=pretix.cfg .venv/bin/python -m pretix analytics_resync --organizer <org> --all
```

## Verifying a build

Before deploying, a couple of quick sanity checks never hurt:

```bash
# 1. Confirm the plugin has not grown any writes to Pretix core tables.
python scripts/check_isolation.py

# 2. Run Django's standard system checks against your Pretix install.
python -m pretix check
```

Both should complete silently with no warnings.

## Roadmap

See [ROADMAP.md](ROADMAP.md).

## Versioning & Changelog

Releases follow [Semantic Versioning](https://semver.org). See
[CHANGELOG.md](CHANGELOG.md) for the history of user-visible changes.

## License

Apache License 2.0. See [LICENSE](LICENSE) for the full text.
