"""V3 self-contained observability — tracing.

Exports traces via OTLP/HTTP when `OTLP_ENDPOINT` is set (Honeycomb, Grafana
Cloud, etc.). Without it, falls back to `ConsoleSpanExporter` — visible in
`fly logs` but only with ~30 minutes of retention.

`OTLP_HEADERS` carries vendor credentials, comma-separated `key=value`
(e.g. `x-honeycomb-team=...,x-honeycomb-dataset=hooktrace`).
"""

import logging
import os

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


def _parse_otlp_headers(raw: str | None) -> dict[str, str]:
    """Parse the comma-separated `key=value` env var into a dict."""
    if not raw:
        return {}
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        out[key.strip()] = value.strip()
    return out


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

    otlp_endpoint = os.environ.get("OTLP_ENDPOINT")
    if otlp_endpoint:
        headers = _parse_otlp_headers(os.environ.get("OTLP_HEADERS"))
        exporter = OTLPSpanExporter(
            endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces",
            headers=headers,
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        logger.info(
            "otlp_tracing_configured",
            extra={"endpoint": otlp_endpoint, "service": service_name},
        )
    else:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        logger.info(
            "otlp_tracing_stdout_fallback",
            extra={"service": service_name, "reason": "OTLP_ENDPOINT not set"},
        )

    trace.set_tracer_provider(provider)
    logger.info("tracing_configured", extra={"service": service_name})


def instrument_app(app: FastAPI, engine: AsyncEngine | None = None) -> None:
    """Auto-instrument FastAPI + SQLAlchemy. Safe to call once at startup."""
    FastAPIInstrumentor.instrument_app(app)
    if engine is not None:
        SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
