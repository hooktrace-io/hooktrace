"""arq Worker process. Activated in prod via worker.fly.toml's
[processes] worker = "arq webhook_inspector.jobs.worker.WorkerSettings".

This module is imported by the arq CLI on worker startup. The class body
deliberately does NOT call Settings() — that happens in on_startup() once,
when arq has booted its event loop and env is populated. This matches the
no-module-level-side-effects rule established for the web/ingestor apps.
"""

import logging
import os
from typing import Any, ClassVar
from uuid import UUID

from arq.connections import RedisSettings
from arq.cron import cron
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from webhook_inspector.config import Settings
from webhook_inspector.infrastructure.database.session import make_engine
from webhook_inspector.jobs.abuse_scan import run_abuse_scan
from webhook_inspector.observability.logging import configure_logging
from webhook_inspector.observability.metrics import configure_metrics, force_flush_metrics
from webhook_inspector.observability.tracing import configure_tracing

logger = logging.getLogger(__name__)


# Why os.environ.get() and not Settings() : arq reads `WorkerSettings.redis_settings`
# as an INSTANCE (RedisSettings | None), not a callable. So the value must exist
# at class-body evaluation. We MUST avoid Settings() here because Settings.database_url
# is required (no default) — any test importing this module without DATABASE_URL
# set raises ValidationError at the `import` line. Reading REDIS_URL directly via
# os.environ has no required fields and falls back to a localhost DSN for dev.
#
# Tests that need a specific REDIS_URL set it in conftest.py at session scope
# BEFORE the worker module is first imported.
_REDIS_DSN = os.environ.get("REDIS_URL", "redis://localhost:6379")


async def execute_forward(ctx: dict[str, Any], forward_id_str: str) -> None:
    """arq job wrapper for ExecuteForward use case.

    Builds the use case per-job (fresh DB session per invocation) using
    stateless deps stashed on ctx at startup. Catches all uncaught exceptions,
    logs them, and returns — the use case owns the retry budget via
    claim_for_attempt + record_outcome; we do not let arq's max_tries
    silently swallow failures.
    """
    from webhook_inspector.application.use_cases.execute_forward import ExecuteForward
    from webhook_inspector.infrastructure.http.safe_replay_target import make_safe_replay_target
    from webhook_inspector.infrastructure.queue.arq_forward_queue import ArqForwardQueue
    from webhook_inspector.infrastructure.repositories.endpoint_repository import (
        PostgresEndpointRepository,
    )
    from webhook_inspector.infrastructure.repositories.forward_repository import (
        PostgresForwardRepository,
    )
    from webhook_inspector.infrastructure.repositories.request_repository import (
        PostgresRequestRepository,
    )

    session_factory: async_sessionmaker[AsyncSession] = ctx["_session_factory"]
    async with session_factory() as session:
        try:
            use_case = ExecuteForward(
                endpoint_repo=PostgresEndpointRepository(session),
                request_repo=PostgresRequestRepository(session),
                forward_repo=PostgresForwardRepository(session),
                forward_queue=ArqForwardQueue(ctx["redis"]),
                target=make_safe_replay_target(),
                blob_storage=ctx["_blob_storage"],
                metrics=ctx["_metrics_collector"],
                secrets_key=ctx["_secrets_key"],
            )
            await use_case.execute(forward_id=UUID(forward_id_str))
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception(
                "execute_forward_uncaught",
                extra={"forward_id": forward_id_str},
            )


async def startup(ctx: dict[str, object]) -> None:
    """arq on_startup hook — equivalent of FastAPI lifespan.
    Wires logging + tracing + metrics so the worker is observable the same
    way as web/ingestor (structlog JSON output to fly logs). Stashes a DB
    engine + session factory + blob storage + metrics collector on ctx so
    arq job wrappers can reuse them without rebuilding per invocation.
    """
    import base64

    settings = Settings()
    configure_logging(settings.log_level, settings.service_name + "-worker")
    configure_tracing(settings.service_name + "-worker", settings.environment)
    configure_metrics(service_name=settings.service_name + "-worker")
    logger.info("worker_startup", extra={"service": settings.service_name + "-worker"})

    engine = make_engine(settings)
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    ctx["_engine"] = engine
    ctx["_session_factory"] = session_factory

    from webhook_inspector.infrastructure.storage.factory import make_blob_storage

    blob_storage = make_blob_storage(settings)

    import opentelemetry.metrics as otel_metrics

    from webhook_inspector.infrastructure.observability.otel_metrics_collector import (
        OtelMetricsCollector,
    )

    meter = otel_metrics.get_meter("webhook-inspector-worker")
    metrics_collector = OtelMetricsCollector(meter)

    secrets_key = (
        base64.b64decode(settings.secrets_encryption_key)
        if settings.secrets_encryption_key
        else b""
    )

    ctx["_blob_storage"] = blob_storage
    ctx["_metrics_collector"] = metrics_collector
    ctx["_settings"] = settings
    ctx["_secrets_key"] = secrets_key


async def shutdown(ctx: dict[str, object]) -> None:
    """arq on_shutdown hook — flush OTEL metrics and dispose the DB engine.
    CLAUDE.md: short-lived jobs (and any process that may be killed
    by Fly machine rotation) MUST force_flush_metrics() or the last interval's
    datapoints are lost.
    """
    try:
        from sqlalchemy.ext.asyncio import AsyncEngine

        engine = ctx.get("_engine")
        if isinstance(engine, AsyncEngine):
            await engine.dispose()
    except Exception:  # noqa: BLE001
        logger.warning("worker_engine_dispose_failed", exc_info=True)
    force_flush_metrics()
    logger.info("worker_shutdown")


class WorkerSettings:
    """arq introspects class attributes to build the worker. Keep this thin:
    everything that needs env / I/O happens in on_startup, not here.
    """

    # RedisSettings INSTANCE, not a callable — arq introspects this as a value.
    # Computed at module import time from the REDIS_URL env var (no Settings()
    # dependency, so DATABASE_URL is not required to import this module).
    redis_settings = RedisSettings.from_dsn(_REDIS_DSN)

    functions: ClassVar[list[object]] = [execute_forward]

    # Daily abuse scan at 03:30 UTC. run_at_startup=False keeps the scan
    # off the cold-start path — we don't want a worker rotation to trigger
    # a full Postgres scan + Discord notification.
    cron_jobs: ClassVar[list[Any]] = [
        cron(run_abuse_scan, hour=3, minute=30, run_at_startup=False),
    ]

    # Retry policy. NOT 1: a non-zero margin lets arq pick up a job whose
    # function crashed before its own retry logic ran (e.g. OOM, SIGTERM
    # mid-call). The domain advisory-lock retry layer still owns the actual
    # retry budget; this is the floor.
    max_tries = 2

    # job_timeout bounds the worker's worst-case wait on the advisory lock —
    # under the worker's max_jobs concurrency, two arq workers racing on the
    # same (endpoint, integration, event_type) key serialize via Postgres,
    # capped at this timeout. Tune in tandem with the forwarding retry config.
    job_timeout = 120

    # Concurrency cap per worker process. 256 MB VM x ~5 MB per concurrent
    # forward (body + httpx connection + working memory) = ~50 max, but
    # default of 10 leaves headroom for OTEL/Postgres pool. Tune in PR10
    # once we have real metrics.
    max_jobs = 10

    on_startup = startup
    on_shutdown = shutdown
