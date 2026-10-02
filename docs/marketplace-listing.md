# Event Analytics — marketplace listing draft

**Name:** Event Analytics
**PyPI package:** pretix-event-analytics
**Vendor:** Hexadexa
**License:** Apache 2.0 (free)
**Source:** https://github.com/h3x4d3x4/Pretix-Event-Analytics
**Requirements:** pretix 2026.2 or later, Python 3.10–3.12. Self-hosted pretix (event + organizer plugin).
**Languages:** English, German, Portuguese (Portugal)

## Short description (one line)
Sales against previous editions, first-timers and returning visitors, audience and check-in for recurring events — without storing personal data.

## Description
Built for festivals and other events that come back every year. Group your editions into a series and see how this year compares with the last ones — at the same number of days before the event.

- **Overview** — revenue, tickets, average order, returning buyers, refunds and check-in, each compared like-for-like with the previous edition; sales pace of every edition and a forecast.
- **Sales** — tickets and revenue over time, split by product, country or new vs returning; price-tier changes and your own moments ("line-up announced") on the timeline; weekday × hour heatmap.
- **Loyalty** — how many people come for the first time, who came back, who hasn't (yet), and win-back lists as order codes. Pick any set of editions to see how many people came once, twice, three times.
- **Audience** — where buyers live, nationality, age, language, payment methods, group size. Exact and derived countries are kept apart and labelled.
- **Tickets & Operations** — quotas, products, add-ons, vouchers, payment completion, check-in curve, no-shows, refunds, opt-in question breakdowns.
- **Resale** — tickets that changed hands (TicketSwap plugin and name changes), counted once per ticket.
- **Exports** — CSV, a full PDF report and two exporters in pretix's Export menu.
- **Past editions** — import attendee lists from before pretix so earlier visitors count as returning.
- **Alerts** — optional e-mail when sales fall behind the previous edition.

## Privacy
People are matched across editions on name + birth date only and stored as keyed HMAC hashes — never in plain text. A shared card or e-mail never merges two ticket holders. When in doubt, a ticket stays "unknown" instead of guessed. Exports contain order codes, not names or addresses. Includes a data shredder for pretix's GDPR deletion. The plugin never writes ticketing data.

## Screenshots
docs/screenshots/overview.png · sales.png · loyalty.png · audience.png (generated demo data)

## Setup in short
`pip install pretix-event-analytics`, `python -m pretix migrate`, set `secret_salt` in `pretix.cfg`, enable the plugin for the organizer and each event, create a series, run a resync. Full instructions in the README.
