import asyncio
import logging
import sys

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from webhook_inspector.config import Settings
from webhook_inspector.domain.ports.metrics_collector import MetricsCollector
from webhook_inspector.infrastructure.repositories.endpoint_repository import (
    PostgresEndpointRepository,
)
from webhook_inspector.observability.logging import configure_logging
from webhook_inspector.observability.tracing import configure_tracing

logger = logging.getLogger(__name__)


async def run_cleanup(
    database_url: str,
    metrics: MetricsCollector | None = None,
) -> int:
    url = (
        database_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://")
        if "+psycopg://" in database_url
        else database_url
    )
    engine = create_async_engine(url, future=True)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            deleted = await PostgresEndpointRepository(session).delete_expired()
            await session.commit()
            logger.info("cleanup_complete", extra={"deleted": deleted})
            if metrics is not None:
                metrics.cleaner_run(deleted)
            return deleted
    finally:
        await engine.dispose()


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level, settings.service_name + "-cleaner")
    configure_tracing(settings.service_name + "-cleaner", settings.environment)

    # Wire metrics (lazy import — short-lived job, keep boot fast)
    from opentelemetry import metrics as otel_metrics

    from webhook_inspector.infrastructure.observability.otel_metrics_collector import (
        OtelMetricsCollector,
    )
    from webhook_inspector.observability.metrics import (
        configure_metrics,
        force_flush_metrics,
    )

    configure_metrics(service_name=settings.service_name + "-cleaner")
    collector = OtelMetricsCollector(otel_metrics.get_meter("webhook-inspector-cleaner"))

    try:
        deleted = asyncio.run(run_cleanup(settings.database_url, metrics=collector))
        sys.stdout.write(f"deleted {deleted} expired endpoints\n")
    finally:
        # Critical: short-lived job must flush metrics before exit.
        force_flush_metrics()


if __name__ == "__main__":
    main()
