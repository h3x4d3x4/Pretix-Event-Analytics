# pretix Event Analytics

Advanced analytics plugin for [Pretix](https://pretix.eu) with cross-edition repeat buyer tracking, cohort retention matrix, predictive scoring, and secondary market detection.

## Features

- **Repeat Buyer Detection** — Identifies returning attendees across editions using HMAC-SHA256 identity hashing (email, payment fingerprints). No raw PII stored.
- **Cohort Retention Matrix** — Shows what percentage of buyers from each edition returned to later editions.
- **Predictive Scoring** — Deterministic 0–100 score predicting likelihood of a buyer returning next year.
- **Sales Velocity & Pacing** — Cumulative revenue by days-until-event, compared across editions.
- **Geographical LTV Heatmap** — Revenue and order counts by country with lifetime value comparison.
- **Add-on Attach Rates** — Ticket add-on purchase rates by buyer segment (new vs. returning, age group).
- **Early Bird / Last Minute Personas** — Purchase timing analysis with revenue and retention breakdown.
- **Secondary Market Tracking** — Detects likely ticket resales via attendee name changes in Pretix audit logs. Typo corrections and middle-name additions are automatically filtered out.
- **Multi-edition Dashboard Filtering** — Filter or merge data across multiple editions of the same event series.
- **CSV & PDF Export** — Download full analytics data.

## Requirements

- Pretix 2024.x or later
- Python 3.10+
- Django 4.2+
- Redis (for Celery task queue — optional, falls back to synchronous processing)

## Installation

```bash
pip install pretix-event-analytics
```

Then add `pretix_event_analytics` to `INSTALLED_APPS` in your Pretix configuration, or install it as a Pretix plugin in the admin panel.

Run migrations:

```bash
python -m pretix migrate
```

## Configuration

1. In the Pretix control panel, navigate to your event → **Settings → Analytics**.
2. Create an **Event Series** (organizer level) to group related editions.
3. Link the event to a series and assign its edition year.
4. Set the **Home Country** (ISO alpha-2 code) to enable local vs. international buyer scoring.
5. Save, then click **Resync** on the dashboard to import existing orders.

## Usage

### Dashboard

Navigate to **Event → Analytics** in the Pretix control panel. The dashboard auto-updates when new orders are paid.

### Resync

Use the **Resync** button on the dashboard, or run the CLI:

```bash
# Import all paid orders
python -m pretix analytics_resync --event <event-slug> --organizer <organizer-slug>

# After the event ends — incorporate check-in data into predictive scores
python -m pretix analytics_resync --event <event-slug> --organizer <organizer-slug> --checkin
```

### Access Control

The analytics dashboard requires `can_view_orders` permission. Configuration and resync require `can_change_event_settings`. Both are standard Pretix team permissions.

## Privacy

- Email addresses are converted to HMAC-SHA256 hashes before storage and are never recoverable.
- The salt is derived from Django's `SECRET_KEY` by default, or from `PRETIX_ANALYTICS_SECRET_SALT` if set.
- No names, addresses, or other personal data are stored in the analytics tables.

> **Important:** Changing `SECRET_KEY` or `PRETIX_ANALYTICS_SECRET_SALT` after orders have been ingested will break repeat buyer detection for existing records. Run a full resync after any salt change.

## Data Model

```
EventSeries (organizer-level grouping)
  └── EventAnalyticsConfig (per-event: series + edition year + home country)
        └── AnalyticsOrderFact (one row per order — all dashboard queries target this)
              ├── AnalyticsTicketFact (one row per ticket position)
              └── AnalyticsIdentity (hashed identity signals for repeat detection)
```

## License

Apache Software License 2.0
