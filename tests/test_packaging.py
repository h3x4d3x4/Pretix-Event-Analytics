"""The built wheel must survive Pretix's production static build."""
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def wheel(tmp_path_factory):
    out = tmp_path_factory.mktemp("wheel")
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(out), str(ROOT)], check=True)
    return next(out.glob("*.whl"))


def test_static_references_resolve_inside_wheel(wheel):
    """
    Pretix runs collectstatic with ManifestStaticFilesStorage, which follows
    sourceMappingURL and url() references and fails on any missing file
    (this broke the first production build: the Chart.js source map is not
    shipped).
    """
    with zipfile.ZipFile(wheel) as z:
        names = set(z.namelist())
        for name in names:
            if not name.startswith("pretix_event_analytics/static/") or not name.endswith((".js", ".css")):
                continue
            text = z.read(name).decode("utf-8", errors="ignore")
            base = name.rsplit("/", 1)[0]
            refs = re.findall(r"sourceMappingURL=([^\s*]+)", text)
            refs += [u for u in re.findall(r"url\(['\"]?([^'\")]+)['\"]?\)", text)
                     if not u.startswith(("data:", "http", "#", "/"))]
            for ref in refs:
                target = f"{base}/{ref.split('?')[0].split('#')[0]}"
                assert target in names, f"{name} references {ref}, which is not in the wheel"


def test_wheel_contents(wheel):
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
    assert not any(n.endswith(".map") for n in names)
    assert not any(n.startswith(("tests/", "scripts/")) for n in names)
    for required in ("pretix_event_analytics/migrations/0008_pace_alerts.py",
                     "pretix_event_analytics/templates/pretix_event_analytics/pages/loyalty.html",
                     "pretix_event_analytics/static/pretix_event_analytics/dashboard.js"):
        assert required in names
