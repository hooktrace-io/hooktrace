"""V3 self-contained observability — tracing.

No external trace backend in V3. All spans propagate to:
1. InMemoryRequestSpanProcessor — buffered per-trace summaries for the
   timeline UI (cf. PR5).
2. ConsoleSpanExporter — dev visibility, harmless in prod.

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

from webhook_inspector.observability.span_summary_processor import (
    InMemoryRequestSpanProcessor,
)

logger = logging.getLogger(__name__)

# Module-level singleton — handlers grab the processor via get_summary_processor()
# to call pop_summary(trace_id).
_summary_processor: InMemoryRequestSpanProcessor | None = None


def get_summary_processor() -> InMemoryRequestSpanProcessor:
    global _summary_processor
    if _summary_processor is None:
        _summary_processor = InMemoryRequestSpanProcessor()
    return _summary_processor


def configure_tracing(service_name: str, environment: str) -> None:
    """Build + install the global TracerProvider.

    Idempotent : calling twice replaces the previous provider.
    """
    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment": environment,
        }
    )
    provider = TracerProvider(resource=resource, sampler=ALWAYS_ON)
    provider.add_span_processor(get_summary_processor())
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    logger.info("tracing_configured", extra={"service": service_name})


def instrument_app(app: FastAPI, engine: AsyncEngine | None = None) -> None:
    """Auto-instrument FastAPI + SQLAlchemy. Safe to call once at startup."""
    FastAPIInstrumentor.instrument_app(app)
    if engine is not None:
        SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
