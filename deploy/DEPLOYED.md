# Production deployment log

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
