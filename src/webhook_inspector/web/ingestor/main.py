from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from webhook_inspector.config import Settings
from webhook_inspector.observability.logging import configure_logging
from webhook_inspector.observability.metrics import configure_metrics
from webhook_inspector.observability.tracing import configure_tracing, instrument_app
from webhook_inspector.web._secrets_key import _validate_secrets_key
from webhook_inspector.web.ingestor.deps import _engine
from webhook_inspector.web.ingestor.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    configure_logging(settings.log_level, settings.service_name + "-ingestor")
    configure_tracing(
        settings.service_name + "-ingestor",
        settings.environment,
        cloud_trace_enabled=settings.cloud_trace_enabled,
        otlp_endpoint=settings.otlp_endpoint,
        otlp_headers=settings.otlp_headers,
        sample_ratio=settings.trace_sample_ratio,
    )
    configure_metrics(
        service_name=settings.service_name + "-ingestor",
        cloud_metrics_enabled=settings.cloud_metrics_enabled,
        otlp_endpoint=settings.otlp_endpoint,
        otlp_headers=settings.otlp_headers,
    )
    instrument_app(app, _engine())
    # Validate secrets key at startup so a misconfigured deploy fails fast.
    _validate_secrets_key(settings.secrets_encryption_key)
    yield


app = FastAPI(title="Webhook Inspector — Ingestor", lifespan=lifespan)
app.include_router(router)
