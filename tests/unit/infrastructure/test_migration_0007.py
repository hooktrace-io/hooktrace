"""Structure test for migration 0007 — replay.

Verifies the migration module can be imported cleanly and exposes the
expected Alembic attributes + callable upgrade/downgrade functions.
No DB connection required.
"""

import importlib
import inspect


def test_migration_0007_imports_cleanly() -> None:
    mod = importlib.import_module("migrations.versions.0007_c0073b4e15ab_replay")
    assert mod is not None


def test_migration_0007_revision_identifiers() -> None:
    mod = importlib.import_module("migrations.versions.0007_c0073b4e15ab_replay")
    assert mod.revision == "c0073b4e15ab"
    assert mod.down_revision == "f88fffb1c697"


def test_migration_0007_has_upgrade_and_downgrade() -> None:
    mod = importlib.import_module("migrations.versions.0007_c0073b4e15ab_replay")
    assert callable(mod.upgrade), "upgrade() must be a callable"
    assert callable(mod.downgrade), "downgrade() must be a callable"
    assert len(inspect.signature(mod.upgrade).parameters) == 0
    assert len(inspect.signature(mod.downgrade).parameters) == 0


def test_migration_0007_upgrade_has_check_constraint_name() -> None:
    """upgrade() source must reference the CHECK constraint name."""
    mod = importlib.import_module("migrations.versions.0007_c0073b4e15ab_replay")
    src = inspect.getsource(mod.upgrade)
    assert "replays_outcome_xor" in src


def test_migration_0007_upgrade_has_index_name() -> None:
    """upgrade() source must reference the index name."""
    mod = importlib.import_module("migrations.versions.0007_c0073b4e15ab_replay")
    src = inspect.getsource(mod.upgrade)
    assert "ix_replays_request_id" in src


def test_migration_0007_upgrade_has_xor_expression() -> None:
    """upgrade() source must contain the exact XOR CHECK expression."""
    mod = importlib.import_module("migrations.versions.0007_c0073b4e15ab_replay")
    src = inspect.getsource(mod.upgrade)
    assert "(status_code IS NOT NULL) <> (error IS NOT NULL)" in src
