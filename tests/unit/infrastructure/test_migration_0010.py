"""Structure test for migration 0010 — forwards.

Verifies the migration module can be imported cleanly and exposes the
expected Alembic attributes + callable upgrade/downgrade functions.
No DB connection required.
"""

import importlib
import inspect


def test_migration_0010_imports_cleanly() -> None:
    mod = importlib.import_module("migrations.versions.0010_72e54acb55c0_forwards")
    assert mod is not None


def test_migration_0010_revision_identifiers() -> None:
    mod = importlib.import_module("migrations.versions.0010_72e54acb55c0_forwards")
    assert mod.revision == "72e54acb55c0"
    assert mod.down_revision == "9a1f0c4b5d7e"


def test_migration_0010_has_upgrade_and_downgrade() -> None:
    mod = importlib.import_module("migrations.versions.0010_72e54acb55c0_forwards")
    assert callable(mod.upgrade), "upgrade() must be a callable"
    assert callable(mod.downgrade), "downgrade() must be a callable"
    assert len(inspect.signature(mod.upgrade).parameters) == 0
    assert len(inspect.signature(mod.downgrade).parameters) == 0


def test_migration_0010_upgrade_has_check_constraint_name() -> None:
    """upgrade() source must reference the CHECK constraint name."""
    mod = importlib.import_module("migrations.versions.0010_72e54acb55c0_forwards")
    src = inspect.getsource(mod.upgrade)
    assert "forwards_status_check" in src


def test_migration_0010_upgrade_has_dead_index_name() -> None:
    """upgrade() source must reference forwards_dead_idx."""
    mod = importlib.import_module("migrations.versions.0010_72e54acb55c0_forwards")
    src = inspect.getsource(mod.upgrade)
    assert "forwards_dead_idx" in src


def test_migration_0010_upgrade_has_endpoint_index_name() -> None:
    """upgrade() source must reference forwards_endpoint_idx."""
    mod = importlib.import_module("migrations.versions.0010_72e54acb55c0_forwards")
    src = inspect.getsource(mod.upgrade)
    assert "forwards_endpoint_idx" in src
