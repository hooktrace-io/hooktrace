"""Structure test for migration 0006 — schema_inference.

Verifies the migration module can be imported cleanly and exposes the
expected Alembic attributes + callable upgrade/downgrade functions.
No DB connection required.
"""

import importlib
import inspect


def test_migration_0006_imports_cleanly() -> None:
    mod = importlib.import_module("migrations.versions.0006_f88fffb1c697_schema_inference")
    assert mod is not None


def test_migration_0006_revision_identifiers() -> None:
    mod = importlib.import_module("migrations.versions.0006_f88fffb1c697_schema_inference")
    assert mod.revision == "f88fffb1c697"
    assert mod.down_revision == "4113bf01bbf7"


def test_migration_0006_has_upgrade_and_downgrade() -> None:
    mod = importlib.import_module("migrations.versions.0006_f88fffb1c697_schema_inference")
    assert callable(mod.upgrade), "upgrade() must be a callable"
    assert callable(mod.downgrade), "downgrade() must be a callable"
    assert len(inspect.signature(mod.upgrade).parameters) == 0
    assert len(inspect.signature(mod.downgrade).parameters) == 0


def test_migration_0006_upgrade_has_integration_check() -> None:
    """upgrade() source must reference the integration CHECK constraint name."""
    mod = importlib.import_module("migrations.versions.0006_f88fffb1c697_schema_inference")
    src = inspect.getsource(mod.upgrade)
    assert "inferred_schemas_integration_check" in src


def test_migration_0006_upgrade_has_key_index() -> None:
    """upgrade() source must reference the unique index name AND the exact
    COALESCE expression that the ON CONFLICT clause depends on.
    """
    mod = importlib.import_module("migrations.versions.0006_f88fffb1c697_schema_inference")
    src = inspect.getsource(mod.upgrade)
    assert "inferred_schemas_key_idx" in src
    # The conflict-target expression must match this string byte-for-byte
    # (Postgres requires expression equality, not just semantic equivalence).
    assert "COALESCE(event_type, '')" in src
