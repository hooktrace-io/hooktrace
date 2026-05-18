import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from webhook_inspector.config import Settings
from webhook_inspector.infrastructure.notifications.postgres_notifier import PostgresNotifier
from webhook_inspector.observability.logging import configure_logging
from webhook_inspector.observability.metrics import configure_metrics
from webhook_inspector.observability.tracing import configure_tracing, instrument_app
from webhook_inspector.web._secrets_key import _validate_secrets_key
from webhook_inspector.web.app import deps as app_deps
from webhook_inspector.web.app.deps import _engine
from webhook_inspector.web.app.routes import router
from webhook_inspector.web.app.template_globals import apply_globals

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
apply_globals(templates.env)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    configure_logging(settings.log_level, settings.service_name + "-app")
    configure_tracing(settings.service_name + "-app", settings.environment)
    configure_metrics(service_name=settings.service_name + "-app")

    # Validate secrets key at startup so a misconfigured deploy fails fast.
    _validate_secrets_key(settings.secrets_encryption_key)

    # Build notifier once and store on app.state so request-scoped deps can read it.
    sync_dsn = settings.database_url.replace("+psycopg_async", "").replace("+psycopg", "")
    notifier = PostgresNotifier(dsn=sync_dsn)
    await notifier.start()
    app.state.notifier = notifier

    instrument_app(app, _engine())

    # Wire ForwardQueue for operator-driven Retry / Redrive actions on the DLQ
    # page. The web app enqueues into the same Redis pool the worker drains;
    # in dev (no REDIS_URL) get_forward_queue() falls back to NullForwardQueue.
    if settings.redis_url:
        from arq import create_pool
        from arq.connections import RedisSettings

        from webhook_inspector.infrastructure.queue.arq_forward_queue import ArqForwardQueue

        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        app_deps._forward_queue_singleton = ArqForwardQueue(pool)

    # Background task: sample active endpoints count every 60s
    task = asyncio.create_task(_active_endpoints_gauge_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await notifier.stop()

        # Shutdown: close the Redis pool if we opened one.
        if app_deps._forward_queue_singleton is not None:
            from webhook_inspector.infrastructure.queue.arq_forward_queue import (
                ArqForwardQueue,
            )

            if isinstance(app_deps._forward_queue_singleton, ArqForwardQueue):
                await app_deps._forward_queue_singleton._pool.aclose()
            app_deps._forward_queue_singleton = None


async def _active_endpoints_gauge_loop() -> None:
    """Update the active endpoints gauge every 60s via the repository."""
    from opentelemetry import metrics as otel_metrics
    from opentelemetry.metrics import Observation

    from webhook_inspector.infrastructure.repositories.endpoint_repository import (
        PostgresEndpointRepository,
    )
    from webhook_inspector.web.app.deps import _session_factory

    meter = otel_metrics.get_meter("webhook-inspector-app")
    last_value = {"v": 0}

    def _callback(_options):  # type: ignore[no-untyped-def]
        return [Observation(last_value["v"])]

    meter.create_observable_gauge(
        "webhook_inspector.endpoints.active",
        callbacks=[_callback],
        description="Endpoints not yet expired.",
    )

    factory = _session_factory()
    while True:
        try:
            async with factory() as s:
                repo = PostgresEndpointRepository(s)
                last_value["v"] = await repo.count_active()
        except Exception:  # noqa: BLE001 — best-effort gauge: any DB/network error must not crash the background loop
            pass  # gauge stays at previous value
        await asyncio.sleep(60)


app = FastAPI(title="Webhook Inspector — App", lifespan=lifespan)
app.state.templates = templates
app.include_router(router)
