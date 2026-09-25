# Enable + configure analytics for the SUTI organizer only. Idempotent.
# Run: docker compose exec -T -u pretixuser pretix python3 -m pretix shell < setup_analytics.py
from django_scopes import scopes_disabled
from pretix.base.models import Organizer
from pretix_event_analytics.models import EventAnalyticsConfig, EventSeries

PLUGIN = "pretix_event_analytics"
EDITIONS = {"2020": 2022, "2023": 2023, "2024": 2024, "2026": 2026}  # event slug -> edition year ("3023" = test copy, left out)

with scopes_disabled():
    org = Organizer.objects.get(slug="suti")
    plugins = [p for p in (org.plugins or "").split(",") if p]
    if PLUGIN not in plugins:
        org.plugins = ",".join(plugins + [PLUGIN]); org.save(update_fields=["plugins"])
    series, _ = EventSeries.objects.get_or_create(organizer=org, slug="suti-festival", defaults={"name": "Suti Festival"})
    for ev in org.events.filter(slug__in=list(EDITIONS)):
        ep = [p for p in (ev.plugins or "").split(",") if p]
        if PLUGIN not in ep:
            ev.plugins = ",".join(ep + [PLUGIN]); ev.save(update_fields=["plugins"])
        cfg, created = EventAnalyticsConfig.objects.update_or_create(
            event=ev, defaults={"series": series, "edition_year": EDITIONS[ev.slug], "home_country": "PT", "is_active": True})
        print("configured", ev.slug, "->", EDITIONS[ev.slug], "(new)" if created else "(updated)")
    other = [e.slug for o in Organizer.objects.exclude(pk=org.pk) for e in o.events.all() if PLUGIN in (e.plugins or "")]
    print("organizer plugins:", org.plugins, "| enabled outside suti:", other or "none")
