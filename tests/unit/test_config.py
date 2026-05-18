import os
from unittest.mock import patch

from webhook_inspector.config import Settings


def test_settings_read_from_env():
    env = {
        "DATABASE_URL": "postgresql+psycopg://u:p@h:5432/db",
        "BLOB_STORAGE_PATH": "/tmp/blobs",
        "ENDPOINT_TTL_DAYS": "7",
        "MAX_BODY_BYTES": "1048576",
        "BODY_INLINE_THRESHOLD_BYTES": "4096",
    }
    with patch.dict(os.environ, env, clear=True):
        s = Settings()
        assert s.database_url == "postgresql+psycopg://u:p@h:5432/db"
        assert s.blob_storage_path == "/tmp/blobs"
        assert s.endpoint_ttl_days == 7
        assert s.max_body_bytes == 1048576
        assert s.body_inline_threshold_bytes == 4096


def test_settings_have_sensible_defaults_for_local():
    env = {"DATABASE_URL": "postgresql+psycopg://u:p@h:5432/db"}
    with patch.dict(os.environ, env, clear=True):
        s = Settings()
        assert s.endpoint_ttl_days == 7
        assert s.max_body_bytes == 10 * 1024 * 1024
        assert s.body_inline_threshold_bytes == 8 * 1024
        assert s.environment == "local"


def test_settings_defaults_local_blob_backend():
    s = Settings(database_url="postgresql+psycopg://x@y/z")
    assert s.blob_storage_backend == "local"
    assert s.s3_bucket_name is None


def test_settings_accepts_s3_backend(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x@y/z")
    monkeypatch.setenv("BLOB_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET_NAME", "wi-blobs")
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://acc.r2.cloudflarestorage.com")
    s = Settings()
    assert s.blob_storage_backend == "s3"
    assert s.s3_bucket_name == "wi-blobs"
    assert s.s3_endpoint_url == "https://acc.r2.cloudflarestorage.com"


def test_secrets_encryption_key_optional(monkeypatch):
    """Settings.secrets_encryption_key defaults to None for backward compat
    (cleaner job + tests that don't need it). PR1 use cases that require it
    will validate len(base64.b64decode(...)) == 32 themselves.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x@y/z")
    from webhook_inspector.config import Settings

    s = Settings()
    assert s.secrets_encryption_key is None


def test_secrets_encryption_key_from_env(monkeypatch):
    import base64

    key_b64 = base64.b64encode(b"\x00" * 32).decode()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x@y/z")
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", key_b64)
    from webhook_inspector.config import Settings

    s = Settings()
    assert s.secrets_encryption_key == key_b64
