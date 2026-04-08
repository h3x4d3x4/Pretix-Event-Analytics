# Pretix Event Analytics

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Pretix](https://img.shields.io/badge/Pretix-2025.x%2B-purple.svg)](https://pretix.eu)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://python.org)

Analytics plugin for [Pretix](https://pretix.eu) that provides cross-edition repeat buyer tracking, cohort retention analysis, predictive scoring, and a full analytics dashboard.

## Features

- **Repeat Buyer Detection** &mdash; Identifies returning attendees across editions using HMAC-SHA256 identity matching (email, Stripe card fingerprints, PayPal payer IDs, bank IBANs, name+DOB composites). No raw PII is stored.
- **Cohort Retention Matrix** &mdash; Forward-looking retention heatmap showing what percentage of buyers from each edition returned to later editions.
- **Predictive Repeat Score** &mdash; Deterministic 0&ndash;100 scoring model based on purchase history, timing, geography, group size, and check-in data. Computed at payment time, refined after the event.
- **Full Analytics Dashboard** &mdash; Revenue KPIs, country/age/language breakdowns, sales pacing charts, buyer persona segmentation (Early Bird / Regular / Last Minute), add-on attach rates, caravan/camping analysis, and secondary market (ticket resale) detection.
- **Multi-Edition Filtering** &mdash; Filter or merge analytics data across multiple editions of the same event series.
- **CSV & PDF Export** &mdash; Download filtered analytics data or a printable summary report.
- **GDPR-Safe by Design** &mdash; All buyer identification uses salted HMAC-SHA256 hashes. No emails, names, or ID numbers touch the analytics tables. Includes a data shredder for GDPR deletion requests.
- **Management Command** &mdash; `analytics_resync` for bulk re-processing via CLI.

## Requirements

| Component | Version |
|-----------|---------|
| Pretix    | 2025.x or later |
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

1. Log in to the Pretix control panel
2. Go to your **Organizer** settings &rarr; **Plugins**
3. Enable **Event Analytics**

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

For events with existing orders, trigger a resync:

**From the dashboard:** Click the **Resync** button.

**From the command line:**

```bash
# Single event
python -m pretix analytics_resync --event <organizer>/<event>

# All events in a series
python -m pretix analytics_resync --series <organizer>/<series-slug>

# All configured events
python -m pretix analytics_resync --all

# Include check-in data (run after the event has ended)
python -m pretix analytics_resync --event <organizer>/<event> --checkin

# Preview without processing
python -m pretix analytics_resync --all --dry-run
```

### 4. View the dashboard

Navigate to **Event &rarr; Analytics** in the control panel. The dashboard auto-updates as new orders are paid.

## Architecture

### Data Flow

```
Pretix Signals (order_paid, order_canceled, checkin_created)
        |
        v
  Celery Background Tasks (async, with retry + backoff)
        |
        v
  Normalization Layer (all PII hashed or bucketed)
        |
        v
  AnalyticsOrderFact + AnalyticsTicketFact + AnalyticsIdentity
        |
        v
  Dashboard / CSV / PDF
```

New orders are processed asynchronously via Celery. If Celery is not configured, Pretix falls back to synchronous processing automatically.

### Identity Resolution

Repeat detection uses multiple identity signals, ranked by confidence:

| Signal | Source | Confidence |
|--------|--------|------------|
| Stripe card fingerprint | Payment metadata | High |
| PayPal payer ID | Payment metadata | High |
| Bank IBAN | Payment metadata | High |
| Name + Date of Birth | Attendee questions | High |
| Email address | Order email | Medium |

All values are HMAC-SHA256 hashed before storage. A buyer is flagged as "repeat" if **any** identity matches a previous edition in the same series.

### Predictive Scoring

| Factor | Points |
|--------|--------|
| Attended a previous edition | +40 |
| Checked in at this event | +20 |
| Purchased 30+ days early | +15 |
| Bought 3+ tickets | +10 |
| Local buyer (same country) | +10 |
| Group order (2+ tickets) | +5 |
| **Maximum** | **100** |

Scores are computed at payment time (without check-in) and recomputed post-event via resync with `--checkin`.

## Access Control

| Permission | Access |
|------------|--------|
| `can_view_orders` | View the analytics dashboard, export CSV/PDF |
| `can_change_event_settings` | Configure analytics, trigger resync |
| `can_change_organizer_settings` | Manage event series |

These are standard Pretix team permissions.

## Privacy & GDPR

- Email addresses and payment fingerprints are converted to HMAC-SHA256 hashes &mdash; never stored in plain text and not reversible.
- Birth dates are converted to age buckets (e.g. "25-34") &mdash; the raw date is never stored.
- Names are never stored in analytics tables.
- The HMAC salt is derived from Django's `SECRET_KEY` by default, or from `PRETIX_ANALYTICS_SECRET_SALT` if explicitly configured.
- A **data shredder** is registered so organisers can delete all analytics data for an event through Pretix's built-in GDPR data export/deletion interface.

> **Note:** Changing `SECRET_KEY` or `PRETIX_ANALYTICS_SECRET_SALT` after orders have been ingested will break repeat buyer detection for existing records. Run a full resync (`--all`) after any salt change.

## Configuration

Add to your `pretix.cfg` or Django settings:

```ini
# Custom HMAC salt for identity hashing (optional).
# If not set, a salt is automatically derived from SECRET_KEY.
# Minimum 16 characters. Do not change after orders are ingested.
PRETIX_ANALYTICS_SECRET_SALT = "your-long-random-string-here-32-chars-minimum"
```

## Data Model

```
EventSeries (organizer-level grouping)
  +-- EventAnalyticsConfig (per-event: series + edition year + home country)
        +-- AnalyticsOrderFact (one row per order, all dashboard queries target this)
              |-- AnalyticsTicketFact (one row per ticket/position)
              +-- AnalyticsIdentity (hashed identity signals for repeat detection)
```

## Project Structure

```
pretix_event_analytics/
|-- models.py                  # EventSeries, EventAnalyticsConfig,
|                              #   AnalyticsOrderFact, AnalyticsTicketFact, AnalyticsIdentity
|-- signals.py                 # order_paid, order_canceled, checkin_created, nav signals
|-- tasks.py                   # Celery tasks with retry + exponential backoff
|-- views.py                   # Dashboard, config, series CRUD, export views
|-- forms.py                   # Config, filter, and series forms
|-- filters.py                 # Queryset filtering helpers
|-- exporters.py               # CSV and PDF export
|-- shredder.py                # GDPR data shredder
|-- services/
|   |-- normalizer.py          # Order -> fact dict orchestration
|   |-- hash_service.py        # HMAC-SHA256 hashing
|   |-- repeat_detector.py     # Cross-edition identity matching
|   |-- predictor.py           # Deterministic repeat probability scorer
|   |-- cohort_service.py      # Retention matrix builder (cached)
|   |-- country_resolver.py    # Payment/invoice/question country detection
|   |-- age_bucketer.py        # Birth date -> age bucket
|   |-- van_length_bucketer.py # Caravan length -> size bucket
|   |-- secondary_market.py    # Name change / ticket resale detection
|   +-- resync_service.py      # Bulk re-processing (chunked, memory-safe)
|-- management/commands/
|   +-- analytics_resync.py    # CLI resync command
|-- migrations/
|-- templates/                 # Dashboard, config, series management, PDF report
|-- templatetags/              # Custom template filters
+-- static/                    # Dashboard CSS/JS, Chart.js
```

## License

Apache License 2.0. See [LICENSE](LICENSE) for the full text.
