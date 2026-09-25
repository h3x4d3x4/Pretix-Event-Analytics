# Production deployment log

**How production is built now:** `~/suti-upgrade/build/` on suti0 describes the full image
(Pretix 2026.7.0 + TicketSwap + SUTI theme + analytics). The theme agent and analytics share it.
To upgrade analytics: `bash ~/suti-analytics/upgrade_analytics.sh <old> <new>` — swaps only the analytics
wheel, allows only analytics migrations (3rd arg = expected analytics migration count, reversed on rollback), dump + fingerprint + page checks + auto-rollback, then updates the
shared folder. **TicketSwap belongs to Sena — never modify its wheel or Dockerfile line.**
Coordinate with whoever is working on the theme before building or restarting.

## 2026-09-25 19:31 UTC — analytics 2.1.1 (on SUTI theme 1.0.4) — deployed and verified by dev-84

Resale showed 0 TicketSwap resales: prod runs TicketSwap **1.0.4**, which records swaps in its own `TicketSwapSwap`
table, not on the ticket (`meta_info`, TicketSwap 2.x). Now reads both (read-only: position, date, success only).
Resale counts admission tickets only. No migration (stays at 0009), no resync needed.

- Prod copy: fingerprint identical, all analytics pages 200 + themed, widgets safe, derived countries 0 leaks /
  0 of 197 contradicting.
- Resale vs TicketSwap's own table (admission tickets): 2026 TicketSwap 79 = 79, manual 26, both 4;
  2024 / 2023 / 2022: TicketSwap 0, manual 31 / 39 / 34. Monthly chart 2026-04 → 2026-08.
- Live: switch 19:31:08 UTC, image `:2026.7.0-analytics2.1.1-theme1.0.4`; revert target
  `:2026.7.0-analytics2.1.0-theme1.0.4` (nothing to undo); dump `~/suti-upgrade/backup/pretix-pre-analytics211-*.dump`.
  `~/suti-upgrade/build` = 2.1.1 + theme 1.0.4 + TicketSwap 1.0.4.
- ID-document country is now on for 2026: 382 orders from it; exact-unknown countries 2026 = 163 (was 347 on 2.1.0).
- **suti0 disk at 93% (2.9 GB free)** — old images/dumps need cleaning up (Andrei).

## 2026-09-25 19:00 UTC — analytics 2.1.0 (on SUTI theme 1.0.4) — deployed and verified by dev-a2

People matched on name + birth date only (when in doubt unknown); customers on order e-mail only; country with
source + derived (inferred/probable) countries in separate fields; PayPal v2 country; combined resale (TicketSwap
read-only + manual name changes); widget HTML fix. Migration 0009 (analytics only).

- **Rehearsal** on a fresh prod dump in a throwaway DB: 0009 up/down/up OK, ticket fingerprint identical each step;
  inferred country contradicting the customer's known country 0/152; nothing derived leaked into `country_code`;
  all analytics pages 200 and themed; widgets render (24% returning buyers / 80% first-time attendees).
- **Live:** switch 19:00:26 UTC, image `:2026.7.0-analytics2.1.0-theme1.0.4` (previous `:2026.7.0-analytics2.0.3`),
  dump `~/suti-upgrade/backup/pretix-pre-analytics210-20260925-190014.dump`. `migrate --check` clean, fingerprint
  identical, no log errors. Resync `--organizer suti --all`: 266 + 557 + 627 + 1,103 orders, 0 skipped, ~1m40.
- **Countries** (orders | exact unknown | inferred | probable | still unknown; before = unknown on 2.0.3):

  | Edition | Orders | Exact unknown | Inferred | Probable | Still unknown | Before |
  |---|---|---|---|---|---|---|
  | 2026 | 1,103 | 347 | 73 | 16 | 258 | 454 |
  | 2024 | 627 | 218 | 29 | 13 | 176 | 300 |
  | 2023 | 557 | 232 | 30 | 18 | 184 | 302 |
  | 2022 (event `2020`) | 266 | 99 | 20 | 7 | 72 | 100 |

  2026 exact sources: card issuer 397, invoice 175, PayPal account 107, card billing 77. Most remaining unknowns
  are free orders (children's tickets) — only a "Country of residence" checkout question will cover them.
- **Rollback (in order):** stop pretix → run the NEW image with `--entrypoint python3 -m pretix migrate
  pretix_event_analytics 0008_pace_alerts` (live conf/data) → tag `:2026.7.0-analytics2.0.3` as latest →
  `up -d --no-build`. Last resort: restore the dump.
- **Open (Andrei):** set the ID-number question for 2026 in Settings → "Where people come from" (opt-in), then resync.

## 2026-09-25 16:58 UTC — analytics 2.0.3 (on SUTI theme 1.0.4)

Stripe card country + fingerprint now read from the PaymentIntent (`charges.data[]`): before, none of the
1,214 Stripe payments yielded them. Resync after deploy: 2,553 orders, 0 skipped. Orders with a known
country 56% (was ~15%); 1,217 card identities link people across e-mail changes. First-timer share 2026:
77.9% of people / 76.4% of buyers (unchanged — the gap to the ~50% survey is real).
Image `:2026.7.0-analytics2.0.3`, rollback `:rollback-pre-analytics2.0.3`; `~/suti-upgrade/build` holds 2.0.3.

## 2026-09-25 16:44 / 16:47 UTC — analytics 2.0.1 and 2.0.2

Theme-proof colours (all `--pa-*` variables, settings/series pages scoped), author "Hexadexa", and a
separate `--pa-accent` for UI states. Via `upgrade_analytics.sh`: 0 migrations, fingerprint identical,
all analytics pages 200 for Administrators. Image `:2026.7.0-analytics2.0.2` (on theme 1.0.3);
rollback `:rollback-pre-analytics2.0.2`. `~/suti-upgrade/build` updated to the 2.0.2 wheel.

## 2026-09-25 — pretix_event_analytics 2.0.0 on tickets.sutifestival.com (suti0)

**Result:** live since 16:25 UTC. Pretix 2026.7.0 + SUTI theme 1.0.2 + TicketSwap 1.0.4 + analytics 2.0.0.
Ticket-data fingerprint identical before/after; `migrate --check` clean; every analytics page renders 200.

- **Image:** `suti-pretix-pretix:latest` = `:2026.7.0-analytics`, built from `~/suti-analytics/build/Dockerfile`
  (the theme agent's `~/suti-upgrade/build/Dockerfile` + two lines installing the analytics wheel).
  **Any future image build must keep those two lines**, or the plugin disappears (data stays, unused).
- **Salt:** `[pretix_event_analytics] secret_salt` in `/etc/pretix/pretix.cfg` (volume `pretixconf/`), generated on the
  server, never printed. **Never change or remove it** — every returning-people figure depends on it.
  Pre-salt config backup: `pretixconf/pretix.cfg.bak-pre-analytics`.
- **Enabled for:** organizer `suti` and events `2020` (edition 2022), `2023`, `2024`, `2026`; series `suti-festival`.
  Not enabled for event `3023` (non-live test copy) or the `rythm`, `residents`, `test` organizers.
- **Initial sync:** `analytics_resync --organizer suti --all` — 2,553 orders, 0 skipped, 81 s.
- **Backups:** `~/suti-analytics/backup/pretix-pre-analytics-20260925-162247.dump` (+ fingerprint) and the nightly VPS backup.

**Rollback** (plugin tables stay but are unused by the previous image):
```sh
cd /opt/docker/suti-pretix
docker tag suti-pretix-pretix:rollback-pre-analytics suti-pretix-pretix:latest
docker compose up -d --no-build pretix
```

### What went wrong on the way (fixed before this deploy)
1. First build failed in `make production`: Chart.js referenced a source map that is not shipped. Nothing changed on the server.
2. Second attempt started, but plugin migrations pinned `pretixbase 0298` by its Pretix-2026.2 name; 2026.7 has a different
   0298, so Django's migration graph was invalid (blocks *all* migrations, core included). Rolled back within minutes, no DB
   changes. Fixed by depending on `pretixbase 0001_initial` only, plus tests that load the real migration graph and build the
   wheel; the deploy script now runs a read-only `migrate --plan` of the new image before stopping anything and rolls back
   unless exactly the expected migrations are applied.

### Not done / open
- Pretix mail host is still a placeholder (`smtp-server-later`), so pace-alert e-mails cannot be sent until mail is set up.
- Imported past-edition lists (e.g. 2019): none yet — Series page → "Past editions from before Pretix".
