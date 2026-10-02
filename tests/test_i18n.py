"""Translations ship, and cached reports never leak one user's language to another."""
import gettext
import pathlib
import shutil
import subprocess

import pytest
from django.urls import reverse

from pretix_event_analytics.services.resync_service import resync_series

LOCALE = pathlib.Path(__file__).resolve().parent.parent / "pretix_event_analytics" / "locale"


@pytest.mark.parametrize("lang", ["de", "pt_PT"])
def test_compiled_translations_match_their_source(lang, tmp_path):
    # Compares content, not file times (git does not keep those): the shipped .mo must
    # hold exactly what its .po compiles to, so a forgotten msgfmt is caught.
    po, mo = LOCALE / lang / "LC_MESSAGES" / "django.po", LOCALE / lang / "LC_MESSAGES" / "django.mo"
    assert mo.exists(), f"{lang}: compile with msgfmt"
    msgfmt = shutil.which("msgfmt")
    if not msgfmt:
        pytest.skip("GNU gettext (msgfmt) not installed")
    fresh = tmp_path / "fresh.mo"
    subprocess.run([msgfmt, "--check", "-o", str(fresh), str(po)], check=True)
    with mo.open("rb") as shipped, fresh.open("rb") as compiled:
        assert gettext.GNUTranslations(shipped)._catalog == gettext.GNUTranslations(compiled)._catalog, \
            f"{lang}: django.mo is out of date — run msgfmt"


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
