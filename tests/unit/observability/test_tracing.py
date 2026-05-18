from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from webhook_inspector.observability.tracing import configure_tracing


def test_configure_tracing_sets_tracer_provider():
    configure_tracing(service_name="test-svc", environment="test")
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)


def test_configure_tracing_registers_console_exporter():
    configure_tracing(service_name="test-svc", environment="test")
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    multi = provider._active_span_processor  # type: ignore[attr-defined]
    processors = getattr(multi, "_span_processors", [multi])
    assert any(isinstance(p, SimpleSpanProcessor) for p in processors)
