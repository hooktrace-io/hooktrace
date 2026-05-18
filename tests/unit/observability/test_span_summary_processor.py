from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

from webhook_inspector.observability.span_summary_processor import (
    InMemoryRequestSpanProcessor,
)


def test_processor_accumulates_spans_for_a_trace():
    processor = InMemoryRequestSpanProcessor(max_traces=10, max_spans_per_trace=50)
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("capture") as root:
        trace_id = format(root.get_span_context().trace_id, "032x")
        with tracer.start_as_current_span("hmac.validate"):
            pass
        with tracer.start_as_current_span("db.insert"):
            with tracer.start_as_current_span("blob.offload"):
                pass

    summary = processor.pop_summary(trace_id)
    assert len(summary) == 4
    names = [s["name"] for s in summary]
    assert names == ["capture", "hmac.validate", "db.insert", "blob.offload"]
    for span in summary:
        assert "duration_ms" in span
        assert "status" in span
        assert "start_time_ns" in span
        assert "span_id" in span
        assert "depth" in span


def test_processor_pops_clears_buffer():
    processor = InMemoryRequestSpanProcessor()
    provider = TracerProvider()
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("x") as span:
        trace_id = format(span.get_span_context().trace_id, "032x")

    assert len(processor.pop_summary(trace_id)) == 1
    assert processor.pop_summary(trace_id) == []


def test_processor_caps_spans_per_trace():
    processor = InMemoryRequestSpanProcessor(max_spans_per_trace=3)
    provider = TracerProvider()
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("root") as root:
        trace_id = format(root.get_span_context().trace_id, "032x")
        for i in range(10):
            with tracer.start_as_current_span(f"child-{i}"):
                pass

    assert len(processor.pop_summary(trace_id)) == 3


def test_processor_evicts_oldest_when_max_traces_reached():
    processor = InMemoryRequestSpanProcessor(max_traces=2)
    provider = TracerProvider()
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("test")

    trace_ids = []
    for i in range(3):
        with tracer.start_as_current_span(f"trace-{i}") as span:
            trace_ids.append(format(span.get_span_context().trace_id, "032x"))

    assert processor.pop_summary(trace_ids[0]) == []
    assert len(processor.pop_summary(trace_ids[1])) == 1
    assert len(processor.pop_summary(trace_ids[2])) == 1


def test_processor_shutdown_clears_buffer():
    processor = InMemoryRequestSpanProcessor()
    provider = TracerProvider()
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("x") as span:
        trace_id = format(span.get_span_context().trace_id, "032x")

    processor.shutdown()
    assert processor.pop_summary(trace_id) == []


def test_processor_parent_span_id_recorded():
    processor = InMemoryRequestSpanProcessor()
    provider = TracerProvider()
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("parent") as root:
        trace_id = format(root.get_span_context().trace_id, "032x")
        root_span_id = format(root.get_span_context().span_id, "016x")
        with tracer.start_as_current_span("child"):
            pass

    summary = processor.pop_summary(trace_id)
    assert len(summary) == 2
    parent_span = next(s for s in summary if s["name"] == "parent")
    child_span = next(s for s in summary if s["name"] == "child")
    assert parent_span["parent_span_id"] is None
    assert child_span["parent_span_id"] == root_span_id


def test_processor_span_id_recorded():
    processor = InMemoryRequestSpanProcessor()
    provider = TracerProvider()
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("root") as root:
        trace_id = format(root.get_span_context().trace_id, "032x")
        root_span_id = format(root.get_span_context().span_id, "016x")

    summary = processor.pop_summary(trace_id)
    assert len(summary) == 1
    assert summary[0]["span_id"] == root_span_id


def test_processor_depth_computed():
    processor = InMemoryRequestSpanProcessor()
    provider = TracerProvider()
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("root") as root:
        trace_id = format(root.get_span_context().trace_id, "032x")
        with tracer.start_as_current_span("child"):
            with tracer.start_as_current_span("grandchild"):
                pass

    summary = processor.pop_summary(trace_id)
    assert len(summary) == 3
    depths = {s["name"]: s["depth"] for s in summary}
    assert depths["root"] == 0
    assert depths["child"] == 1
    assert depths["grandchild"] == 2


def test_processor_force_flush_returns_true():
    processor = InMemoryRequestSpanProcessor()
    assert processor.force_flush() is True
    assert processor.force_flush(timeout_millis=100) is True
