# Roadmap

Ideas agreed but not scheduled. Newest decisions first.

## Next: 2.3.0 — pretix marketplace

Listing on [marketplace.pretix.eu](https://marketplace.pretix.eu) (new releases are pulled from PyPI).
Before submitting, close the gaps against pretix's
[plugin quality checklist](https://docs.pretix.eu/dev/development/api/quality.html):

- **Security contact** — `SECURITY.md` and a line in the README.
- **Action log** — settings changes, resyncs, series changes and attendee-list imports show up in
  pretix's event / organizer log.
- **Event copy** — copying an event carries over its analytics settings (series, home country,
  tracked questions); the edition year is left for the organiser to set.
- **Translations: German and Portuguese (pt_PT)** — the whole plugin (dashboards, settings, e-mails,
  PDF). Strings are already wrapped; generate `locale/<lang>/LC_MESSAGES/django.po` with
  `makemessages`, translate, and ship the compiled `.mo` files in the wheel. pt_PT was decided
  2026-09-25; German added because most pretix organisers use it.
- **Lint config** — flake8 + isort settings, run locally. (No hosted CI.)

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
