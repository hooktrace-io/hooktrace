"""In-memory span processor that accumulates per-trace summaries for later read.

OpenTelemetry's TracerProvider supports multiple span processors. This one runs
alongside the ConsoleSpanExporter (V3 has no external trace backend) and keeps
a bounded LRU of summaries keyed by trace_id. The web/ingestor handlers call
`pop_summary(trace_id)` post-request to attach the timeline to the persisted
CapturedRequest, then the buffer is freed.

Design constraints:
- Thread-safe (RLock) — OTEL spans can be created from any thread
- Bounded memory: `max_traces` (LRU eviction) + `max_spans_per_trace` (drop excess)
- Span subset: {name, span_id, start_time_ns, duration_ms, status, parent_span_id, depth}
"""

import threading
from collections import OrderedDict
from typing import Any

from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.trace import Span


class InMemoryRequestSpanProcessor(SpanProcessor):
    def __init__(self, max_traces: int = 1000, max_spans_per_trace: int = 50) -> None:
        self._max_traces = max_traces
        self._max_spans_per_trace = max_spans_per_trace
        self._buffer: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._lock = threading.RLock()

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        pass

    def on_end(self, span: ReadableSpan) -> None:
        ctx = span.get_span_context()
        if ctx is None:
            return
        trace_id = format(ctx.trace_id, "032x")

        with self._lock:
            spans = self._buffer.get(trace_id)
            if spans is None:
                if len(self._buffer) >= self._max_traces:
                    self._buffer.popitem(last=False)
                spans = []
                self._buffer[trace_id] = spans
            else:
                self._buffer.move_to_end(trace_id)

            if len(spans) >= self._max_spans_per_trace:
                return

            start_ns = span.start_time or 0
            duration_ns = (span.end_time or 0) - start_ns
            parent_id = format(span.parent.span_id, "016x") if span.parent else None
            spans.append(
                {
                    "name": span.name,
                    "span_id": format(ctx.span_id, "016x"),
                    "start_time_ns": start_ns,
                    "duration_ms": duration_ns / 1_000_000,
                    "status": span.status.status_code.name,
                    "parent_span_id": parent_id,
                }
            )

    def pop_summary(self, trace_id: str) -> list[dict[str, Any]]:
        """Return and clear accumulated spans for this trace_id.

        on_end fires inner-first for nested spans, so the buffer is in reverse
        of declaration order. Sort by start_time_ns to restore parent-first
        (then sibling) order for rendering, then compute depth from the
        parent_span_id chain (O(n), parents always start before children).
        """
        with self._lock:
            spans = self._buffer.pop(trace_id, [])
        spans.sort(key=lambda s: s["start_time_ns"])
        # Compute depth: parent always appears before child after sorting by
        # start_time_ns, so a single pass suffices.
        ids_to_depth: dict[str, int] = {}
        for s in spans:
            parent = s["parent_span_id"]
            if parent is None or parent not in ids_to_depth:
                s["depth"] = 0
            else:
                s["depth"] = ids_to_depth[parent] + 1
            ids_to_depth[s["span_id"]] = s["depth"]
        return spans

    def shutdown(self) -> None:
        with self._lock:
            self._buffer.clear()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:  # noqa: ARG002
        return True
