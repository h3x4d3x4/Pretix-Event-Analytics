# Roadmap

Ideas agreed but not scheduled. Newest decisions first.

## Later

- **Portuguese translation (pt_PT)** of the whole plugin — dashboards, settings,
  e-mails, PDF. Decided 2026-09-25: wanted, but after the 2.0 deployment. The
  templates and Python strings are already wrapped for translation; the work is
  generating `locale/pt_PT/LC_MESSAGES/django.po` with `makemessages` and
  translating it.

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
