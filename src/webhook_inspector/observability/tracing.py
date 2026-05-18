"""V3 self-contained observability — tracing.

No external trace backend in V3. All spans propagate to:
1. ConsoleSpanExporter — dev visibility, harmless in prod.

V4 will reintroduce external export (Honeycomb / Grafana Cloud) with a
SampledBatchSpanProcessor wrapper if/when product needs it.
"""

import logging

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


def configure_tracing(service_name: str, environment: str) -> None:
    """Build + install the global TracerProvider.

    NOT idempotent : OpenTelemetry's `trace.set_tracer_provider` silently
    keeps the first provider installed (logs a warning, does NOT override).
    Call this once at app startup. Tests that need a fresh provider must
    install one themselves before `configure_tracing` is invoked.
    """
    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment": environment,
        }
    )
    provider = TracerProvider(resource=resource, sampler=ALWAYS_ON)
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    logger.info("tracing_configured", extra={"service": service_name})


def instrument_app(app: FastAPI, engine: AsyncEngine | None = None) -> None:
    """Auto-instrument FastAPI + SQLAlchemy. Safe to call once at startup."""
    FastAPIInstrumentor.instrument_app(app)
    if engine is not None:
        SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
