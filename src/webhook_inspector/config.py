from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    blob_storage_path: str = "./blobs"
    blob_storage_backend: Literal["local", "gcs", "s3"] = "local"
    gcs_bucket_name: str | None = None
    s3_endpoint_url: str | None = None
    s3_bucket_name: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_region: str = "auto"
    secrets_encryption_key: str | None = (
        None  # base64-encoded 32 bytes; required for HMAC + Forward secrets
    )
    endpoint_ttl_days: int = 7
    max_body_bytes: int = 10 * 1024 * 1024
    body_inline_threshold_bytes: int = 8 * 1024
    export_max_requests: int = 10_000
    environment: str = "local"
    service_name: str = "webhook-inspector"
    log_level: str = "INFO"
    redis_url: str | None = (
        None  # set as Fly secret on worker (Upstash, rediss://) + locally to redis://localhost:6379 for dev
    )
    # Separate Redis URL for the rate-limit middleware. May (and usually
    # will) point at the same instance as redis_url, but kept as its own
    # setting so the rate limiter can be wired/disabled independently of
    # the forward queue. Read directly via os.environ in module-eval code
    # paths (see web/middleware/rate_limit.py) because Settings() requires
    # database_url and module-eval is too early for test fixtures.
    rate_limit_redis_url: str | None = None
    abuse_webhook_url: str | None = None  # Discord webhook URL; if None, abuse scan logs only
