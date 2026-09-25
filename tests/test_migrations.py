"""
Migrations must work on every supported Pretix version.

Pretix's test settings create tables without running migrations, so this is
checked explicitly: every dependency on a core app must point at a migration
that exists, and plugin migrations may only depend on pretixbase's first
migration (core migration names differ between Pretix releases — pinning
the latest one broke deployment on 2026.7).
"""
import pytest
from django.db.migrations.loader import MigrationLoader


@pytest.fixture(autouse=True)
def real_migrations(settings):
    # Pretix's test settings disable migration modules; re-enable them.
    settings.MIGRATION_MODULES = {}


def test_migration_graph_is_consistent():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    loader.graph.validate_consistency()
    ours = [k for k in loader.disk_migrations if k[0] == "pretix_event_analytics"]
    assert len(ours) >= 8


def test_only_depends_on_first_core_migration():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    for (app, name), migration in loader.disk_migrations.items():
        if app != "pretix_event_analytics":
            continue
        for dep_app, dep_name in migration.dependencies:
            if dep_app == "pretixbase":
                assert dep_name == "0001_initial", f"{name} pins pretixbase.{dep_name}"
