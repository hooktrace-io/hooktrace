"""arq Worker process. Activated in prod via worker.fly.toml's
[processes] worker = "arq webhook_inspector.jobs.worker.WorkerSettings".

This module is imported by the arq CLI on worker startup. The class body
deliberately does NOT call Settings() — that happens in on_startup() once,
when arq has booted its event loop and env is populated. This matches the
no-module-level-side-effects rule established for the web/ingestor apps.
"""

import logging
import os
from typing import ClassVar
from uuid import UUID

from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from webhook_inspector.application.use_cases.update_inferred_schema import (
    UpdateInferredSchema,
)
from webhook_inspector.config import Settings
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


async def startup(ctx: dict) -> None:
    """arq on_startup hook — equivalent of FastAPI lifespan.
    Wires logging + tracing + metrics so the worker is observable the same
    way as web/ingestor (structlog JSON output to fly logs).
    Also constructs the UpdateInferredSchema use case and stashes it on ctx
    so arq job wrappers can reuse it across calls without rebuilding the DB
    engine per job.
    """
    settings = Settings()
    configure_logging(settings.log_level, settings.service_name + "-worker")
    configure_tracing(settings.service_name + "-worker", settings.environment)
    configure_metrics(service_name=settings.service_name + "-worker")
    logger.info("worker_startup", extra={"service": settings.service_name + "-worker"})

    engine = create_async_engine(settings.database_url, pool_pre_ping=True, future=True)
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    ctx["_session_factory"] = session_factory

    from webhook_inspector.infrastructure.storage.factory import make_blob_storage

    blob_storage = make_blob_storage(settings)

    import opentelemetry.metrics as otel_metrics

    from webhook_inspector.infrastructure.observability.otel_metrics_collector import (
        OtelMetricsCollector,
    )

    meter = otel_metrics.get_meter("webhook-inspector-worker")
    metrics_collector = OtelMetricsCollector(meter)

    ctx["_blob_storage"] = blob_storage
    ctx["_metrics_collector"] = metrics_collector
    ctx["_settings"] = settings


async def shutdown(_ctx: dict) -> None:
    """arq on_shutdown hook — flush any pending OTEL metrics before exit.
    CLAUDE.md: short-lived jobs (and any process that may be killed
    by Fly machine rotation) MUST force_flush_metrics() or the last interval's
    datapoints are lost.
    """
    force_flush_metrics()
    logger.info("worker_shutdown")


async def update_inferred_schema(ctx: dict, request_id_str: str) -> None:
    """arq job wrapper for UpdateInferredSchema use case.
    Called by arq with ctx populated by on_startup. Builds a fresh DB session
    (and therefore a fresh transaction) per job invocation — the advisory lock
    is scoped to the transaction lifetime.
    """
    session_factory: async_sessionmaker[AsyncSession] = ctx["_session_factory"]

    from webhook_inspector.infrastructure.repositories.request_repository import (
        PostgresRequestRepository,
    )
    from webhook_inspector.infrastructure.repositories.schema_repository import (
        PostgresSchemaRepository,
    )

    async with session_factory() as session:
        try:
            use_case = UpdateInferredSchema(
                request_repo=PostgresRequestRepository(session),
                schema_repo=PostgresSchemaRepository(session),
                blob_storage=ctx["_blob_storage"],
                metrics=ctx["_metrics_collector"],
            )
            await use_case.execute(UUID(request_id_str))
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception(
                "update_inferred_schema_uncaught",
                extra={"request_id": request_id_str},
            )


class WorkerSettings:
    """arq introspects class attributes to build the worker. Keep this thin:
    everything that needs env / I/O happens in on_startup, not here.
    """

    # RedisSettings INSTANCE, not a callable — arq introspects this as a value.
    # Computed at module import time from the REDIS_URL env var (no Settings()
    # dependency, so DATABASE_URL is not required to import this module).
    redis_settings = RedisSettings.from_dsn(_REDIS_DSN)

    functions: ClassVar[list] = [update_inferred_schema]

    # Retry policy. NOT 1: a non-zero margin lets arq pick up a job whose
    # function crashed before its own retry logic ran (e.g. OOM, SIGTERM
    # mid-call, bug in PR7's setup code). PR7's domain retry layer still
    # owns the actual retry budget; this is the floor.
    max_tries = 2

    # Per-job wall-clock cap. PR4 SafeReplayTarget has timeout=10s. With
    # PR7's likely retry-with-backoff (1s + 2s + 4s + 10s call) up to
    # ~3 attempts, 120s leaves room. Adjust here if PR7 widens the budget.
    job_timeout = 120

    # Concurrency cap per worker process. 256 MB VM x ~5 MB per concurrent
    # forward (body + httpx connection + working memory) = ~50 max, but
    # default of 10 leaves headroom for OTEL/Postgres pool. Tune in PR10
    # once we have real metrics.
    max_jobs = 10

    on_startup = startup
    on_shutdown = shutdown
