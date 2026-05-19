import base64
from collections.abc import AsyncIterator
from functools import lru_cache

from fastapi import Depends, Request
from opentelemetry.metrics import Meter
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from webhook_inspector.application.use_cases.abandon_forward import AbandonForward
from webhook_inspector.application.use_cases.create_endpoint import CreateEndpoint
from webhook_inspector.application.use_cases.export_requests import ExportRequests
from webhook_inspector.application.use_cases.get_forward_stats import GetForwardStats
from webhook_inspector.application.use_cases.list_forwards import ListForwards
from webhook_inspector.application.use_cases.list_integrations import ListIntegrations
from webhook_inspector.application.use_cases.list_requests import ListRequests
from webhook_inspector.application.use_cases.redrive_pending_forwards import (
    RedrivePendingForwards,
)
from webhook_inspector.application.use_cases.replay_request import ReplayRequest
from webhook_inspector.application.use_cases.retry_forward import RetryForward
from webhook_inspector.application.use_cases.update_endpoint_config import UpdateEndpointConfig
from webhook_inspector.config import Settings
from webhook_inspector.domain.ports.forward_queue import ForwardQueue
from webhook_inspector.domain.ports.metrics_collector import MetricsCollector
from webhook_inspector.infrastructure.http.safe_replay_target import SafeReplayTarget
from webhook_inspector.infrastructure.notifications.postgres_notifier import PostgresNotifier
from webhook_inspector.infrastructure.queue.null_forward_queue import NullForwardQueue
from webhook_inspector.infrastructure.repositories.endpoint_repository import (
    PostgresEndpointRepository,
)
from webhook_inspector.infrastructure.repositories.forward_repository import (
    PostgresForwardRepository,
)
from webhook_inspector.infrastructure.repositories.replay_repository import (
    PostgresReplayRepository,
)
from webhook_inspector.infrastructure.repositories.request_repository import (
    PostgresRequestRepository,
)
from webhook_inspector.infrastructure.storage.factory import make_blob_storage

# Module-level singleton set by lifespan when Redis is available.
# None → NullForwardQueue() is returned by get_forward_queue().
_forward_queue_singleton: ForwardQueue | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def _engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(settings.database_url, pool_pre_ping=True, future=True)


@lru_cache(maxsize=1)
def _session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(_engine(), expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    factory = _session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@lru_cache(maxsize=1)
def _meter() -> Meter:
    import opentelemetry.metrics as otel_metrics

    return otel_metrics.get_meter("webhook-inspector-app")


@lru_cache(maxsize=1)
def get_metrics() -> MetricsCollector:
    from webhook_inspector.infrastructure.observability.otel_metrics_collector import (
        OtelMetricsCollector,
    )

    return OtelMetricsCollector(_meter())


async def get_notifier(request: Request) -> PostgresNotifier:
    """Return the PostgresNotifier stored on app.state by the lifespan."""
    return request.app.state.notifier  # type: ignore[no-any-return]


async def get_create_endpoint(
    session: AsyncSession = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> CreateEndpoint:
    return CreateEndpoint(
        repo=PostgresEndpointRepository(session),
        ttl_days=settings.endpoint_ttl_days,
        metrics=get_metrics(),
    )


async def get_list_requests(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ListRequests:
    return ListRequests(
        endpoint_repo=PostgresEndpointRepository(session),
        request_repo=PostgresRequestRepository(session),
    )


async def get_export_requests(
    session: AsyncSession = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ExportRequests:
    return ExportRequests(
        endpoint_repo=PostgresEndpointRepository(session),
        request_repo=PostgresRequestRepository(session),
        blob_storage=make_blob_storage(settings),
        max_requests=settings.export_max_requests,
    )


async def get_list_integrations(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ListIntegrations:
    return ListIntegrations(
        endpoint_repo=PostgresEndpointRepository(session),
        request_repo=PostgresRequestRepository(session),
    )


async def get_replay_request(
    session: AsyncSession = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ReplayRequest:
    return ReplayRequest(
        endpoint_repo=PostgresEndpointRepository(session),
        request_repo=PostgresRequestRepository(session),
        replay_repo=PostgresReplayRepository(session),
        target=SafeReplayTarget(
            blocked_host_suffixes=("hooktrace.io",),
            timeout_seconds=10.0,
            max_response_bytes=256 * 1024,
        ),
        blob_storage=make_blob_storage(settings),
        metrics=get_metrics(),
    )


def get_forward_queue() -> ForwardQueue:
    """Return the singleton ArqForwardQueue if Redis was provisioned at startup,
    else a NullForwardQueue (dev mode without Redis). Never raises.
    """
    if _forward_queue_singleton is not None:
        return _forward_queue_singleton
    return NullForwardQueue()


async def get_list_forwards(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ListForwards:
    return ListForwards(
        endpoint_repo=PostgresEndpointRepository(session),
        forward_repo=PostgresForwardRepository(session),
    )


async def get_forward_stats_use_case(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> GetForwardStats:
    return GetForwardStats(
        endpoint_repo=PostgresEndpointRepository(session),
        forward_repo=PostgresForwardRepository(session),
    )


async def get_retry_forward(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> RetryForward:
    return RetryForward(
        endpoint_repo=PostgresEndpointRepository(session),
        forward_repo=PostgresForwardRepository(session),
        forward_queue=get_forward_queue(),
    )


async def get_abandon_forward(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> AbandonForward:
    return AbandonForward(
        endpoint_repo=PostgresEndpointRepository(session),
        forward_repo=PostgresForwardRepository(session),
    )


async def get_redrive_pending_forwards(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> RedrivePendingForwards:
    return RedrivePendingForwards(
        endpoint_repo=PostgresEndpointRepository(session),
        forward_repo=PostgresForwardRepository(session),
        forward_queue=get_forward_queue(),
    )


async def get_update_endpoint_config(
    session: AsyncSession = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> UpdateEndpointConfig:
    # Key was validated at startup by lifespan. When unset (dev mode), pass an
    # empty sentinel ; the use case will reject signature config writes by
    # virtue of the secret-encryption call failing on a zero-length key.
    key = (
        base64.b64decode(settings.secrets_encryption_key)
        if settings.secrets_encryption_key
        else b""
    )
    return UpdateEndpointConfig(
        endpoint_repo=PostgresEndpointRepository(session),
        secrets_key=key,
    )
