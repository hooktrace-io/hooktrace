import base64
from collections.abc import AsyncIterator
from functools import lru_cache

from fastapi import Depends
from opentelemetry.metrics import Meter
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from webhook_inspector.application.use_cases.capture_request import CaptureRequest
from webhook_inspector.config import Settings
from webhook_inspector.domain.ports.blob_storage import BlobStorage
from webhook_inspector.domain.ports.forward_queue import ForwardQueue
from webhook_inspector.domain.ports.metrics_collector import MetricsCollector
from webhook_inspector.infrastructure.queue.null_forward_queue import NullForwardQueue
from webhook_inspector.infrastructure.repositories.endpoint_repository import (
    PostgresEndpointRepository,
)
from webhook_inspector.infrastructure.repositories.forward_repository import (
    PostgresForwardRepository,
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


@lru_cache(maxsize=1)
def _blob_storage() -> BlobStorage:
    return make_blob_storage(get_settings())


@lru_cache(maxsize=1)
def _meter() -> Meter:
    import opentelemetry.metrics as otel_metrics

    return otel_metrics.get_meter("webhook-inspector-ingestor")


@lru_cache(maxsize=1)
def get_metrics() -> MetricsCollector:
    from webhook_inspector.infrastructure.observability.otel_metrics_collector import (
        OtelMetricsCollector,
    )

    return OtelMetricsCollector(_meter())


async def get_session() -> AsyncIterator[AsyncSession]:
    factory = _session_factory()
    async with factory() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


def get_forward_queue() -> ForwardQueue:
    """Return the singleton ArqForwardQueue if Redis was provisioned at startup,
    else a NullForwardQueue (dev mode without Redis). Never raises.
    """
    if _forward_queue_singleton is not None:
        return _forward_queue_singleton
    return NullForwardQueue()


async def get_capture_request(
    session: AsyncSession = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> CaptureRequest:
    # Key was validated at startup by lifespan. When unset (dev mode), pass an
    # empty sentinel ; the use case skips the decrypt_secret call entirely when
    # endpoint.signature_provider is None, so capture still works unconfigured.
    key = (
        base64.b64decode(settings.secrets_encryption_key)
        if settings.secrets_encryption_key
        else b""
    )
    return CaptureRequest(
        endpoint_repo=PostgresEndpointRepository(session),
        request_repo=PostgresRequestRepository(session),
        blob_storage=_blob_storage(),
        inline_threshold=settings.body_inline_threshold_bytes,
        metrics=get_metrics(),
        secrets_key=key,
        forward_repo=PostgresForwardRepository(session),
        forward_queue=get_forward_queue(),
    )
