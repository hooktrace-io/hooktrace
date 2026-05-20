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


def test_configure_metrics_sets_meter_provider(monkeypatch):
    monkeypatch.delenv("OTLP_ENDPOINT", raising=False)
    configure_metrics(service_name="test-svc")
    provider = metrics.get_meter_provider()
    assert isinstance(provider, MeterProvider)


def test_configure_metrics_uses_console_exporter(monkeypatch):
    monkeypatch.delenv("OTLP_ENDPOINT", raising=False)
    configure_metrics(service_name="test-svc")
    provider = metrics.get_meter_provider()
    assert isinstance(provider, MeterProvider)
    names = _exporter_class_names(provider)
    assert any("ConsoleMetricExporter" in n for n in names), names


def test_configure_metrics_uses_otlp_exporter_when_endpoint_set(monkeypatch):
    """When OTLP_ENDPOINT is set, the OTLP/HTTP exporter is wired in.

    `set_meter_provider` is install-once globally. We patch it locally to
    capture the provider built by `configure_metrics` without colliding with
    any provider already installed by sibling tests.
    """
    monkeypatch.setenv("OTLP_ENDPOINT", "https://api.honeycomb.io")
    monkeypatch.setenv("OTLP_HEADERS", "x-honeycomb-team=fake-key")

    captured: list[MeterProvider] = []

    def _capture(p):
        captured.append(p)

    monkeypatch.setattr(metrics, "set_meter_provider", _capture)
    configure_metrics(service_name="test-svc")

    assert len(captured) == 1
    provider = captured[0]
    assert isinstance(provider, MeterProvider)
    names = _exporter_class_names(provider)
    assert any("OTLPMetricExporter" in n for n in names), names


def test_force_flush_metrics_does_not_raise(monkeypatch):
    monkeypatch.delenv("OTLP_ENDPOINT", raising=False)
    configure_metrics(service_name="test-svc")
    # Should complete without error
    force_flush_metrics(timeout_millis=100)


def test_configure_metrics_accepts_only_service_name(monkeypatch):
    """Verify the simplified 1-arg signature works."""
    monkeypatch.delenv("OTLP_ENDPOINT", raising=False)
    configure_metrics("my-service")
    provider = metrics.get_meter_provider()
    assert isinstance(provider, MeterProvider)
