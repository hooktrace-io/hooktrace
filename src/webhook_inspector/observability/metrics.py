"""V3 self-contained observability — metrics.

ConsoleMetricExporter only. force_flush_metrics() preserved for short-lived
jobs (cleaner). V4 will reintroduce OTLP export with sampled batch if needed.
"""

import logging

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource

logger = logging.getLogger(__name__)


def configure_metrics(service_name: str) -> None:
    """Build + install the global MeterProvider with console exporter."""
    resource = Resource.create({"service.name": service_name})
    reader = PeriodicExportingMetricReader(ConsoleMetricExporter())
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)
    logger.info("metrics_configured", extra={"service": service_name})


def force_flush_metrics(timeout_millis: int = 5000) -> None:
    """Force-flush the MeterProvider — useful for short-lived jobs."""
    provider = metrics.get_meter_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush(timeout_millis=timeout_millis)
