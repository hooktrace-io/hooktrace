from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

from webhook_inspector.observability.tracing import configure_tracing


def test_configure_tracing_sets_tracer_provider(monkeypatch):
    monkeypatch.delenv("OTLP_ENDPOINT", raising=False)
    configure_tracing(service_name="test-svc", environment="test")
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)


def test_configure_tracing_registers_console_exporter(monkeypatch):
    monkeypatch.delenv("OTLP_ENDPOINT", raising=False)
    configure_tracing(service_name="test-svc", environment="test")
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    multi = provider._active_span_processor  # type: ignore[attr-defined]
    processors = getattr(multi, "_span_processors", [multi])
    assert any(isinstance(p, SimpleSpanProcessor) for p in processors)


def test_configure_tracing_uses_otlp_exporter_when_endpoint_set(monkeypatch):
    """When OTLP_ENDPOINT is set, the OTLP/HTTP span exporter is wired in.

    `set_tracer_provider` is install-once globally. We patch it locally to
    capture the provider built by `configure_tracing` without colliding with
    any provider already installed by sibling tests.
    """
    monkeypatch.setenv("OTLP_ENDPOINT", "https://api.honeycomb.io")
    monkeypatch.setenv("OTLP_HEADERS", "x-honeycomb-team=fake-key")

    captured: list[TracerProvider] = []

    def _capture(p):
        captured.append(p)

    monkeypatch.setattr(trace, "set_tracer_provider", _capture)
    configure_tracing(service_name="test-svc", environment="test")

    assert len(captured) == 1
    provider = captured[0]
    assert isinstance(provider, TracerProvider)
    multi = provider._active_span_processor  # type: ignore[attr-defined]
    processors = getattr(multi, "_span_processors", [multi])
    assert any(isinstance(p, BatchSpanProcessor) for p in processors)
