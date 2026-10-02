"""Packaging. Metadata lives in setup.cfg; this file compiles the translations on build."""
import pathlib
import shutil
import subprocess

from setuptools import setup
from setuptools.command.build_py import build_py

LOCALE = pathlib.Path(__file__).resolve().parent / "pretix_event_analytics" / "locale"


class BuildWithTranslations(build_py):
    """Compile locale/*/LC_MESSAGES/django.po to .mo before packaging.

    The compiled .mo files are also committed, so building without GNU gettext
    (no ``msgfmt``) still ships working translations instead of failing.
    """

    def run(self):
        msgfmt = shutil.which("msgfmt")
        for po in sorted(LOCALE.glob("*/LC_MESSAGES/django.po")):
            if msgfmt:
                # -c checks every %(name)s placeholder against the source string.
                subprocess.run([msgfmt, "--check", "-o", str(po.with_suffix(".mo")), str(po)], check=True)
            elif not po.with_suffix(".mo").exists():
                raise RuntimeError(f"{po}: msgfmt (GNU gettext) is needed to compile this translation")
        super().run()


setup(cmdclass={"build_py": BuildWithTranslations})
