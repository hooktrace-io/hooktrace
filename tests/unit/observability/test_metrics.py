from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

from webhook_inspector.observability.metrics import configure_metrics, force_flush_metrics


def _exporter_class_names(provider: MeterProvider) -> list[str]:
    readers = provider._sdk_config.metric_readers  # type: ignore[attr-defined]
    out: list[str] = []
    for r in readers:
        if isinstance(r, PeriodicExportingMetricReader):
            exporter = getattr(r, "_exporter", None)
            if exporter is not None:
                out.append(type(exporter).__name__)
    return out


def test_configure_metrics_sets_meter_provider():
    configure_metrics(service_name="test-svc")
    provider = metrics.get_meter_provider()
    assert isinstance(provider, MeterProvider)


def test_configure_metrics_uses_console_exporter():
    configure_metrics(service_name="test-svc")
    provider = metrics.get_meter_provider()
    assert isinstance(provider, MeterProvider)
    names = _exporter_class_names(provider)
    assert any("ConsoleMetricExporter" in n for n in names), names


def test_force_flush_metrics_does_not_raise():
    configure_metrics(service_name="test-svc")
    # Should complete without error
    force_flush_metrics(timeout_millis=100)


def test_configure_metrics_accepts_only_service_name():
    """Verify the simplified 1-arg signature works."""
    configure_metrics("my-service")
    provider = metrics.get_meter_provider()
    assert isinstance(provider, MeterProvider)
