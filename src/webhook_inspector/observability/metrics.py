"""V3 self-contained observability — metrics.

Exports metrics via OTLP/HTTP when `OTLP_ENDPOINT` is set (Honeycomb,
Grafana Cloud, etc.). Without it, falls back to `ConsoleMetricExporter` —
visible in `fly logs` but only with ~30 minutes of retention.

`force_flush_metrics()` preserved for short-lived jobs (cleaner).
"""

import logging
import os

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    MetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource

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


def configure_metrics(service_name: str) -> None:
    """Build + install the global MeterProvider.

    OTLP/HTTP when `OTLP_ENDPOINT` is set, else console exporter.
    """
    resource = Resource.create({"service.name": service_name})

    otlp_endpoint = os.environ.get("OTLP_ENDPOINT")
    exporter: MetricExporter
    if otlp_endpoint:
        headers = _parse_otlp_headers(os.environ.get("OTLP_HEADERS"))
        exporter = OTLPMetricExporter(
            endpoint=f"{otlp_endpoint.rstrip('/')}/v1/metrics",
            headers=headers,
        )
        logger.info(
            "otlp_metrics_configured",
            extra={"endpoint": otlp_endpoint, "service": service_name},
        )
    else:
        exporter = ConsoleMetricExporter()
        logger.info(
            "otlp_metrics_stdout_fallback",
            extra={"service": service_name, "reason": "OTLP_ENDPOINT not set"},
        )

    reader = PeriodicExportingMetricReader(exporter)
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)
    logger.info("metrics_configured", extra={"service": service_name})


def force_flush_metrics(timeout_millis: int = 5000) -> None:
    """Force-flush the MeterProvider — useful for short-lived jobs."""
    provider = metrics.get_meter_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush(timeout_millis=timeout_millis)
