"""OpenTelemetry-backed MetricsCollector adapter.

Wraps an OTEL Meter. Cardinality is tightly controlled per the spec —
labels limited to `method` (uppercase HTTP verb) and `body_offloaded` (bool).
"""

from opentelemetry.metrics import Meter

from webhook_inspector.domain.ports.metrics_collector import MetricsCollector


class OtelMetricsCollector(MetricsCollector):
    def __init__(self, meter: Meter) -> None:
        self._endpoints_created = meter.create_counter(
            "webhook_inspector.endpoints.created",
            description="Total endpoints created.",
        )
        self._requests_captured = meter.create_counter(
            "webhook_inspector.requests.captured",
            description="Total webhooks captured.",
        )
        self._body_size = meter.create_histogram(
            "webhook_inspector.requests.body_size_bytes",
            description="Captured body size distribution.",
            unit="By",
        )
        self._capture_duration = meter.create_histogram(
            "webhook_inspector.requests.capture_duration_seconds",
            description="Latency from request arrival to capture commit.",
            unit="s",
        )
        self._cleaner_deletions = meter.create_counter(
            "webhook_inspector.cleaner.deletions",
            description="Endpoints deleted by the cleaner.",
        )
        # Heartbeat counter — always +1 on cleaner completion, enables
        # reliable absence-based 'cleaner stale' alerting.
        self._cleaner_runs = meter.create_counter(
            "webhook_inspector.cleaner.runs.completed",
            description="Cleaner job runs completed successfully.",
        )
        self._schema_inference = meter.create_counter(
            "webhook_inspector.schema.inference",
            description="Schema inference outcomes, labelled by status.",
        )
        self._schema_enqueue_failed = meter.create_counter(
            "webhook_inspector.schema.enqueue_failed",
            description="Schema inference enqueue failures (Redis down).",
        )

    def endpoint_created(self) -> None:
        self._endpoints_created.add(1)

    def request_captured(
        self,
        *,
        method: str,
        body_offloaded: bool,
        body_size: int,
        duration_seconds: float,
    ) -> None:
        attrs: dict[str, str | bool] = {"method": method.upper(), "body_offloaded": body_offloaded}
        self._requests_captured.add(1, attrs)
        self._body_size.record(body_size, {"body_offloaded": body_offloaded})
        self._capture_duration.record(duration_seconds)  # no labels — always-true label removed

    def cleaner_run(self, deleted: int) -> None:
        self._cleaner_runs.add(1)
        if deleted > 0:
            self._cleaner_deletions.add(deleted)

    def schema_inference(self, *, status: str) -> None:
        self._schema_inference.add(1, {"status": status})

    def schema_enqueue_failed(self) -> None:
        self._schema_enqueue_failed.add(1)
