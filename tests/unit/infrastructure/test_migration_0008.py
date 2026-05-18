"""Structure test for migration 0008 — trace_summary.

Verifies the migration module can be imported cleanly and exposes the
expected Alembic attributes + callable upgrade/downgrade functions.
No DB connection required.
"""

import importlib
import inspect


def test_migration_0008_imports_cleanly() -> None:
    mod = importlib.import_module("migrations.versions.0008_d397d6d2abcb_trace_summary")
    assert mod is not None


def test_migration_0008_revision_identifiers() -> None:
    mod = importlib.import_module("migrations.versions.0008_d397d6d2abcb_trace_summary")
    assert mod.revision == "d397d6d2abcb"
    assert mod.down_revision == "c0073b4e15ab"


def test_migration_0008_has_upgrade_and_downgrade() -> None:
    mod = importlib.import_module("migrations.versions.0008_d397d6d2abcb_trace_summary")
    assert callable(mod.upgrade), "upgrade() must be a callable"
    assert callable(mod.downgrade), "downgrade() must be a callable"
    assert len(inspect.signature(mod.upgrade).parameters) == 0
    assert len(inspect.signature(mod.downgrade).parameters) == 0


def test_migration_0008_upgrade_adds_trace_summary_column() -> None:
    """upgrade() source must reference trace_summary column."""
    mod = importlib.import_module("migrations.versions.0008_d397d6d2abcb_trace_summary")
    src = inspect.getsource(mod.upgrade)
    assert "trace_summary" in src
