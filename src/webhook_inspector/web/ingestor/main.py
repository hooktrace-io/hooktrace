import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from webhook_inspector.config import Settings
from webhook_inspector.observability.logging import configure_logging
from webhook_inspector.observability.metrics import configure_metrics
from webhook_inspector.observability.tracing import configure_tracing, instrument_app
from webhook_inspector.web._secrets_key import _validate_secrets_key
from webhook_inspector.web.ingestor import deps as ingestor_deps
from webhook_inspector.web.ingestor.deps import _engine, get_metrics
from webhook_inspector.web.ingestor.routes import router
from webhook_inspector.web.middleware.rate_limit import RateLimitMiddleware, _Rule


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    configure_logging(settings.log_level, settings.service_name + "-ingestor")
    configure_tracing(settings.service_name + "-ingestor", settings.environment)
    configure_metrics(service_name=settings.service_name + "-ingestor")
    instrument_app(app, _engine())
    # Validate secrets key at startup so a misconfigured deploy fails fast.
    _validate_secrets_key(settings.secrets_encryption_key)

    if settings.redis_url:
        from arq import create_pool
        from arq.connections import RedisSettings

        from webhook_inspector.infrastructure.queue.arq_forward_queue import ArqForwardQueue

        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        ingestor_deps._forward_queue_singleton = ArqForwardQueue(pool)

    yield

    # Shutdown: release the queue's transport (no-op for NullForwardQueue,
    # closes the Redis pool for ArqForwardQueue).
    if ingestor_deps._forward_queue_singleton is not None:
        await ingestor_deps._forward_queue_singleton.aclose()
        ingestor_deps._forward_queue_singleton = None


app = FastAPI(title="Webhook Inspector — Ingestor", lifespan=lifespan)
# Rate limit wired at module-eval, NOT in lifespan: FastAPI raises
# RuntimeError on add_middleware once the app has started. The middleware
# itself is lazy — providers run on first request, so tests that never
# set RATE_LIMIT_REDIS_URL bypass Redis entirely.
app.add_middleware(
    RateLimitMiddleware,
    redis_url_provider=lambda: os.environ.get("RATE_LIMIT_REDIS_URL"),
    rules={
        # Capture surface = abuse vector → fail-closed (503) if Redis dies.
        "/h/": _Rule(name="ingest", limit=100, window_seconds=60, fail_mode="closed"),
    },
    metrics_provider=get_metrics,
)
app.include_router(router)
