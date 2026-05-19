"""Pin the pool config produced by make_engine.

Future drift on these numbers should be a deliberate decision, not an
accident. A burst test in prod on 2026-05-19 saturated the default
5+10 pool, cascaded into PG max_connections exhaustion, and put the DB
cluster into error state. The constants below were chosen to give
headroom up to ~180 cluster-wide connections (6 processes x 30), which
fits the post-upgrade PG vm size shared-cpu-2x cap.
"""

from sqlalchemy.pool import QueuePool

from webhook_inspector.config import Settings
from webhook_inspector.infrastructure.database.session import make_engine


def _engine(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pwd@localhost/db")
    return make_engine(Settings())


def test_make_engine_pool_size_is_10(monkeypatch):
    engine = _engine(monkeypatch)
    assert isinstance(engine.pool, QueuePool)
    assert engine.pool.size() == 10


def test_make_engine_max_overflow_is_20(monkeypatch):
    engine = _engine(monkeypatch)
    # SQLAlchemy doesn't expose max_overflow via a property; read the kwarg.
    assert engine.pool._max_overflow == 20


def test_make_engine_pool_timeout_is_10(monkeypatch):
    engine = _engine(monkeypatch)
    assert engine.pool._timeout == 10


def test_make_engine_pool_recycle_is_5_minutes(monkeypatch):
    engine = _engine(monkeypatch)
    assert engine.pool._recycle == 300


def test_make_engine_pool_pre_ping_enabled(monkeypatch):
    engine = _engine(monkeypatch)
    assert engine.pool._pre_ping is True
