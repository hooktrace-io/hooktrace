"""arq Worker process. Activated in prod via worker.fly.toml's
[processes] worker = "arq webhook_inspector.jobs.worker.WorkerSettings".

This module is imported by the arq CLI on worker startup. The class body
deliberately does NOT call Settings() — that happens in on_startup() once,
when arq has booted its event loop and env is populated. This matches the
no-module-level-side-effects rule established for the web/ingestor apps.
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID

from arq.connections import RedisSettings
from arq.cron import cron
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

if TYPE_CHECKING:
    from webhook_inspector.infrastructure.repositories.endpoint_repository import (
        PostgresEndpointRepository,
    )
    from webhook_inspector.infrastructure.repositories.forward_repository import (
        PostgresForwardRepository,
    )
    from webhook_inspector.infrastructure.repositories.request_repository import (
        PostgresRequestRepository,
    )

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

    ExecuteForward now manages its own session lifecycle (claim TX, HTTP
    with no DB, record TX). The wrapper passes a ``unit_of_work`` factory
    that opens a fresh session per phase. Defensive try/except catches
    uncaught exceptions so arq's max_tries doesn't silently swallow them
    — the use case owns the retry budget via claim/record_outcome.
    """
    from webhook_inspector.application.use_cases.execute_forward import ExecuteForward
    from webhook_inspector.infrastructure.http.safe_replay_target import make_safe_replay_target
    from webhook_inspector.infrastructure.queue.arq_forward_queue import ArqForwardQueue

    session_factory: async_sessionmaker[AsyncSession] = ctx["_session_factory"]
    try:
        use_case = ExecuteForward(
            unit_of_work=lambda: _postgres_unit_of_work(session_factory),
            forward_queue=ArqForwardQueue(ctx["redis"]),
            target=make_safe_replay_target(),
            blob_storage=ctx["_blob_storage"],
            metrics=ctx["_metrics_collector"],
            secrets_key=ctx["_secrets_key"],
        )
        await use_case.execute(forward_id=UUID(forward_id_str))
    except Exception:
        logger.exception(
            "execute_forward_uncaught",
            extra={"forward_id": forward_id_str},
        )


@asynccontextmanager
async def _postgres_unit_of_work(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[
    tuple[
        "PostgresForwardRepository",
        "PostgresEndpointRepository",
        "PostgresRequestRepository",
    ]
]:
    """Open a session, build the three repos bound to it, commit on
    successful exit, rollback on exception. Matches the contract of
    ``ForwardUnitOfWork`` declared in the application layer.
    """
    from webhook_inspector.infrastructure.repositories.endpoint_repository import (
        PostgresEndpointRepository,
    )
    from webhook_inspector.infrastructure.repositories.forward_repository import (
        PostgresForwardRepository,
    )
    from webhook_inspector.infrastructure.repositories.request_repository import (
        PostgresRequestRepository,
    )

    async with session_factory() as session:
        try:
            yield (
                PostgresForwardRepository(session),
                PostgresEndpointRepository(session),
                PostgresRequestRepository(session),
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise


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

    # Prod fail-fast: without REDIS_URL the worker has nothing to drain
    # (arq's RedisSettings would still bind, but it would point at the
    # default localhost — silently disconnected from the production queue
    # populated by ingestor/web). Worker doesn't read the rate-limit
    # Redis, so RATE_LIMIT_REDIS_URL is intentionally not checked here.
    if settings.environment == "prod" and not settings.redis_url:
        raise RuntimeError(
            "REDIS_URL is required in production but is not set. "
            "Without it, the worker cannot drain the forward queue."
        )

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
