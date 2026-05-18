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
        self._replay_attempt = meter.create_counter(
            "webhook_inspector.replay.attempt_total",
            description="Replay attempt outcomes, labelled by status.",
        )
        self._ssrf_block = meter.create_counter(
            "webhook_inspector.ssrf.block_total",
            description="SSRF guard rejections, labelled by reason.",
        )
        self._ssrf_dns_validation = meter.create_counter(
            "webhook_inspector.ssrf.dns_validation_total",
            description="SSRF DNS validation outcomes, labelled by result.",
        )
        self._forward_attempt = meter.create_counter(
            "webhook_inspector.forward.attempt_total",
            description="Forward attempt outcomes, labelled by status.",
        )
        self._forward_enqueue_failed = meter.create_counter(
            "webhook_inspector.forward.enqueue_failed_total",
            description="Best-effort enqueue failures (Redis unreachable at capture time).",
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

    def replay_attempt(self, *, status: str) -> None:
        self._replay_attempt.add(1, {"status": status})

    def ssrf_block(self, *, reason: str) -> None:
        self._ssrf_block.add(1, {"reason": reason})

    def ssrf_dns_validation(self, *, result: str) -> None:
        self._ssrf_dns_validation.add(1, {"result": result})

    def forward_attempt(self, *, status: str) -> None:
        self._forward_attempt.add(1, {"status": status})

    def forward_enqueue_failed(self) -> None:
        self._forward_enqueue_failed.add(1)
