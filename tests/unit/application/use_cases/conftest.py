"""Shared test fixtures for use-case span tests.

We need an active TracerProvider with at least one exporter to capture spans
emitted by the use cases. We call ``configure_tracing()`` (the production
bootstrap function) at session scope so that:

1. The global OTEL proxy is satisfied with a real provider exactly once.
2. test_tracing.py sees the same provider regardless of test ordering.
3. The ``InMemoryRequestSpanProcessor`` singleton is registered as expected.

Each test then receives a fresh ``InMemorySpanExporter`` added on top of the
already-configured provider, so span assertions are isolated.
"""

from collections.abc import Iterator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from webhook_inspector.observability.tracing import configure_tracing


@pytest.fixture(scope="session", autouse=True)
def _install_tracing_provider() -> None:
    """Ensure configure_tracing() runs before any test in this package.

    If a real provider was already installed (e.g. by a previous conftest),
    this is a no-op — configure_tracing internally calls set_tracer_provider
    which OTEL ignores silently. We still get the production provider ordering
    if we're first.
    """
    configure_tracing(service_name="test-use-cases", environment="test")


@pytest.fixture
def span_exporter() -> Iterator[InMemorySpanExporter]:
    """Return a fresh InMemorySpanExporter added to the active provider.

    The provider is always a real TracerProvider here because
    ``_install_tracing_provider`` runs before any test in this package.

    Shutdown the exporter on teardown so dead processors don't accumulate
    on the shared provider across the test session.
    """
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider), (
        f"Expected TracerProvider, got {type(provider).__name__}"
    )
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    exporter.shutdown()
