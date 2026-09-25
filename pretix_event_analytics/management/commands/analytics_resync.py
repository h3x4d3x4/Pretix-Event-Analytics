"""
Management command: analytics_resync

Rebuilds analytics fact tables from raw Pretix order data.
Must be run after initial plugin installation, after changing event series
configuration, or after updating the SECRET_SALT.

Usage:
    # Single event
    python -m pretix analytics_resync --event suti-festival-2024 --organizer suti

    # Full series (all editions)
    python -m pretix analytics_resync --series suti-festival --organizer suti

    # All configured events (use with care on large installations)
    python -m pretix analytics_resync --all

Options:
    --checkin    Deprecated no-op: check-ins are always included now.
                 Use this after an event has finished.
    --dry-run    Print what would be processed without making changes.
"""
import logging

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Rebuild analytics fact tables from Pretix order data."

    def add_arguments(self, parser):
        scope = parser.add_mutually_exclusive_group(required=True)
        scope.add_argument(
            "--event",
            type=str,
            metavar="EVENT_SLUG",
            help="Resync a single event by its slug.",
        )
        scope.add_argument(
            "--series",
            type=str,
            metavar="SERIES_SLUG",
            help="Resync all editions in a series by series slug.",
        )
        scope.add_argument(
            "--all",
            action="store_true",
            help="Resync all configured events.",
        )

        parser.add_argument(
            "--organizer",
            type=str,
            metavar="ORGANIZER_SLUG",
            help="Filter by organizer slug (recommended to avoid ambiguity).",
        )
        parser.add_argument(
            "--checkin",
            action="store_true",
            help="Deprecated (check-ins are always included). Kept for script compatibility.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List events that would be processed without making any changes.",
        )

    def handle(self, *args, **options):
        from django_scopes import scopes_disabled
        from pretix.base.models import Event

        from ...models import EventAnalyticsConfig, EventSeries

        dry_run = options["dry_run"]
        include_checkin = options["checkin"]
        organizer_slug = options.get("organizer")

        # ── Resolve target events ─────────────────────────────────────────────
        if options["event"]:
            with scopes_disabled():
                qs = Event.objects.filter(slug=options["event"])
                if organizer_slug:
                    qs = qs.filter(organizer__slug=organizer_slug)
                events = list(qs.select_related("organizer"))
            if not events:
                raise CommandError(
                    f"No event found with slug '{options['event']}'"
                    + (f" for organizer '{organizer_slug}'" if organizer_slug else "")
                )

        elif options["series"]:
            series_qs = EventSeries.objects.filter(slug=options["series"])
            if organizer_slug:
                series_qs = series_qs.filter(organizer__slug=organizer_slug)
            series_list = list(series_qs)
            if not series_list:
                raise CommandError(f"No series found with slug '{options['series']}'")
            configs = EventAnalyticsConfig.objects.filter(
                series__in=series_list
            ).select_related("event__organizer", "series")
            events = [c.event for c in configs]
            if not events:
                raise CommandError(
                    f"No events are linked to series '{options['series']}'. "
                    "Configure them first via the event analytics config panel."
                )

        else:  # --all
            configs = EventAnalyticsConfig.objects.select_related(
                "event__organizer"
            ).all()
            if organizer_slug:
                configs = configs.filter(event__organizer__slug=organizer_slug)
            events = [c.event for c in configs]
            if not events:
                raise CommandError("No configured events found.")

        # ── Dry run: just list ────────────────────────────────────────────────
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] Would resync {len(events)} event(s):"
                )
            )
            for event in events:
                self.stdout.write(f"  • {event.organizer.slug}/{event.slug}")
            return

        # ── Process ───────────────────────────────────────────────────────────
        total = len(events)
        for i, event in enumerate(events, 1):
            self.stdout.write(
                f"[{i}/{total}] Resyncing {event.organizer.slug}/{event.slug} ..."
            )
            try:
                from ...services.resync_service import resync_event

                result = resync_event(
                    event,
                    include_checkin=include_checkin,
                    log_fn=lambda m: self.stdout.write(f"  {m}"),
                    resolve_people=False,
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ Done: {result['processed']} orders processed, "
                        f"{result['skipped']} skipped."
                    )
                )
            except ValueError as exc:
                self.stdout.write(self.style.ERROR(f"  ✗ Config error: {exc}"))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  ✗ Failed: {exc}"))
                logger.exception("analytics_resync failed for event %s", event.slug)

        # Resolve returning people once per series (and per standalone
        # event) after every edition is in place — the result is then
        # independent of the order in which editions were processed.
        from ...services.people import run_scope

        seen_series = set()
        for event in events:
            cfg = EventAnalyticsConfig.objects.select_related("series").filter(event=event).first()
            if cfg and cfg.series:
                key = (cfg.series.organizer_id, cfg.series.slug)
                if key in seen_series:
                    continue
                seen_series.add(key)
                self.stdout.write(f"Resolving returning people for series {cfg.series.slug} ...")
                run_scope(*key)
            elif cfg:
                run_scope(event.organizer_id, "", event.pk)

        self.stdout.write(self.style.SUCCESS(f"\nResync complete for {total} event(s)."))
