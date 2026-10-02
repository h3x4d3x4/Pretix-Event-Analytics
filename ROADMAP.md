# Roadmap

Ideas agreed but not scheduled. Newest decisions first.

## Next

- **pretix marketplace listing** — 2.3.0 closed the checklist gaps (security contact, activity log,
  event copy, German + Portuguese, lint config). Listing text drafted; needs the vendor account.
- **Native review of the translations** — German and Portuguese were machine-assisted; a native
  speaker's pass is welcome (edit `locale/<lang>/LC_MESSAGES/django.po`).

## Maybe

- **Read-only API / spreadsheet feed** — expose the figures the dashboard
  shows (daily sales, returning share, per-edition totals) as a token-protected
  JSON/CSV URL, so a Google Sheet (`IMPORTDATA`) or a BI tool can pull them
  automatically. Only worth it if someone on the team maintains their own
  reports outside Pretix; the CSV exports and the Pretix Export menu already
  cover one-off analysis.

## Decided against

- **Weekly e-mail digest** — not needed (2026-09-25). Pace alerts cover the
  "tell me when something is wrong" case.
