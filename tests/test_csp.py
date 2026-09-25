"""Dashboard scripts must be allowed by Pretix's Content-Security-Policy."""
import re

from django.urls import reverse


def test_dashboard_scripts_satisfy_csp(admin_client, make_edition, series):
    kit = make_edition(2026)
    kit.order("csp@example.org")
    url = reverse("plugins:pretix_event_analytics:sales",
                  kwargs={"organizer": kit.event.organizer.slug, "event": kit.event.slug})
    r = admin_client.get(url)
    csp = r.get("Content-Security-Policy", "")
    assert csp, "Pretix should send a CSP header on control pages"
    script_src = next((d for d in csp.split(";") if d.strip().startswith("script-src")), "")
    html = r.content.decode()
    ours = [t for t in re.findall(r"<script[^>]*>", html) if "pretix_event_analytics" in t or "pa-charts" in t]
    assert len(ours) == 3
    for tag in ours:
        if 'type="application/json"' in tag:
            continue  # data block, never executed
        src = re.search(r'src="([^"]+)"', tag).group(1)
        # Same-origin static file: allowed by 'self' (or by the static URL / nonce).
        nonce = re.search(r'nonce="([^"]*)"', tag)
        allowed = ("'self'" in script_src or src.split("/static/")[0] in script_src
                   or (nonce and nonce.group(1) and f"'nonce-{nonce.group(1)}'" in script_src))
        assert allowed, (tag, script_src)
    # No inline executable script of ours (would need unsafe-inline).
    assert not re.search(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>\s*\S", html.split('class="pa-root"')[1])
    style_src = next((d for d in csp.split(";") if d.strip().startswith("style-src")), "")
    print(script_src, "|", style_src)


def test_no_inline_style_attributes(admin_client, make_edition, series):
    """Pretix's CSP has style-src 'self' (no 'unsafe-inline'): style="" would be ignored."""
    from pretix_event_analytics.services.resync_service import resync_series
    e25, kit = make_edition(2025), make_edition(2026)
    e25.order("a@example.org")
    kit.order("a@example.org", [{"item": kit.ga, "addons": [kit.parking]}])
    resync_series(series)
    org, ev = kit.event.organizer.slug, kit.event.slug
    urls = [reverse(f"plugins:pretix_event_analytics:{n}", kwargs={"organizer": org, "event": ev})
            for n in ("dashboard", "sales", "audience", "loyalty", "tickets", "operations", "resale", "config")]
    urls += [reverse("plugins:pretix_event_analytics:series_list", kwargs={"organizer": org}),
             reverse("plugins:pretix_event_analytics:series_detail", kwargs={"organizer": org, "pk": series.pk})]
    for url in urls:
        html = admin_client.get(url).content.decode()
        start = html.find('class="pa-root"')
        if start < 0:
            start = html.find("<h1")
        ours = html[start:html.rfind("pretix_event_analytics/dashboard.")]
        assert 'style="' not in ours, (url, re.findall(r'<[^>]*style="[^"]*"[^>]*>', ours)[:3])
