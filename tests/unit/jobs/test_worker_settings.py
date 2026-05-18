"""Smoke tests for the arq WorkerSettings class.

Guards the no-module-level-side-effects invariant: the worker module must be
importable without DATABASE_URL being set. If a future maintainer accidentally
puts Settings() back in the class body, these tests raise a pydantic
ValidationError at the import line.
"""

from arq.connections import RedisSettings


def test_worker_settings_importable_without_database_url(monkeypatch):
    """The import must NOT trigger Settings() (which requires DATABASE_URL).
    Reading REDIS_URL directly via os.environ has no required fields and
    falls back to localhost for dev — so the import succeeds even on a bare
    pytest invocation.
    """
    # Intentionally NO DATABASE_URL — pinning the no-Settings-at-import invariant.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import importlib

    import webhook_inspector.jobs.worker as worker_mod

    importlib.reload(worker_mod)

    # functions is a list; empty for now — PR7 will populate it with the
    # forward job.
    assert isinstance(worker_mod.WorkerSettings.functions, list)
    assert worker_mod.WorkerSettings.functions == []
    assert worker_mod.WorkerSettings.max_tries == 2
    assert worker_mod.WorkerSettings.job_timeout == 120
    assert worker_mod.WorkerSettings.max_jobs == 10


def test_redis_settings_is_instance(monkeypatch):
    """WorkerSettings.redis_settings must be a RedisSettings instance (not a
    callable) — arq reads it as a value, not as a factory.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import importlib

    import webhook_inspector.jobs.worker as worker_mod

    importlib.reload(worker_mod)

    assert isinstance(worker_mod.WorkerSettings.redis_settings, RedisSettings)


def test_redis_settings_tls_from_rediss_url(monkeypatch):
    """rediss:// scheme must produce ssl=True on the RedisSettings instance.
    This guards the Upstash production URL (which uses rediss://).
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REDIS_URL", "rediss://default:pw@xx.upstash.io:6379")
    import importlib

    import webhook_inspector.jobs.worker as worker_mod

    importlib.reload(worker_mod)

    assert worker_mod.WorkerSettings.redis_settings.host == "xx.upstash.io"
    assert worker_mod.WorkerSettings.redis_settings.port == 6379
    assert worker_mod.WorkerSettings.redis_settings.ssl is True
