"""Translations ship, and cached reports never leak one user's language to another."""
import pathlib

from django.urls import reverse

from pretix_event_analytics.services.resync_service import resync_series

LOCALE = pathlib.Path(__file__).resolve().parent.parent / "pretix_event_analytics" / "locale"


def test_compiled_translations_are_present_and_current():
    for lang in ("de", "pt_PT"):
        po, mo = LOCALE / lang / "LC_MESSAGES" / "django.po", LOCALE / lang / "LC_MESSAGES" / "django.mo"
        assert mo.exists(), f"{lang}: run msgfmt"
        assert mo.stat().st_mtime >= po.stat().st_mtime, f"{lang}: django.mo is older than django.po"


def test_same_page_in_two_languages(admin_client, make_edition, series):
    kit = make_edition(2026)
    kit.order("a@example.org", country="ES", days_before=20)
    resync_series(series)
    url = reverse("plugins:pretix_event_analytics:audience",
                  kwargs={"organizer": kit.event.organizer.slug, "event": kit.event.slug})
    from pretix.base.models import User
    u = User.objects.get(pk=admin_client.session["_auth_user_id"])

    u.locale = "de"
    u.save()
    de = admin_client.get(url).content.decode()
    u.locale = "pt-pt"
    u.save()
    pt = admin_client.get(url).content.decode()

    assert "Spanien" in de and "Espanha" not in de
    assert "Espanha" in pt and "Spanien" not in pt  # not the cached German page
