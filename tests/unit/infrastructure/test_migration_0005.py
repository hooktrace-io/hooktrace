"""Structure test for migration 0005 — integration_detection.

Verifies the migration module can be imported cleanly and exposes the
expected Alembic attributes + callable upgrade/downgrade functions.
No DB connection required.
"""

import importlib
import inspect


def test_migration_0005_imports_cleanly() -> None:
    mod = importlib.import_module("migrations.versions.0005_4113bf01bbf7_integration_detection")
    assert mod is not None


def test_migration_0005_revision_identifiers() -> None:
    mod = importlib.import_module("migrations.versions.0005_4113bf01bbf7_integration_detection")
    assert mod.revision == "4113bf01bbf7"
    assert mod.down_revision == "2adad4a5f106"


def test_migration_0005_has_upgrade_and_downgrade() -> None:
    mod = importlib.import_module("migrations.versions.0005_4113bf01bbf7_integration_detection")
    assert callable(mod.upgrade), "upgrade() must be a callable"
    assert callable(mod.downgrade), "downgrade() must be a callable"
    # Both must accept zero positional args (alembic calls them with no args)
    assert len(inspect.signature(mod.upgrade).parameters) == 0
    assert len(inspect.signature(mod.downgrade).parameters) == 0
