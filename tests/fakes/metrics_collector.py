"""In-memory MetricsCollector for tests. Records every call."""

from dataclasses import dataclass, field

from webhook_inspector.domain.ports.metrics_collector import MetricsCollector


@dataclass
class CapturedCall:
    method: str
    body_offloaded: bool
    body_size: int
    duration_seconds: float


@dataclass
class FakeMetricsCollector(MetricsCollector):
    endpoints_created_count: int = 0
    captured_calls: list[CapturedCall] = field(default_factory=list)
    cleaner_runs: list[int] = field(default_factory=list)
    schema_inference_calls: list[str] = field(default_factory=list)
    schema_enqueue_failed_count: int = 0
    replay_attempt_calls: list[str] = field(default_factory=list)
    ssrf_block_calls: list[str] = field(default_factory=list)
    ssrf_dns_validation_calls: list[str] = field(default_factory=list)

    def endpoint_created(self) -> None:
        self.endpoints_created_count += 1

    def request_captured(
        self,
        *,
        method: str,
        body_offloaded: bool,
        body_size: int,
        duration_seconds: float,
    ) -> None:
        self.captured_calls.append(
            CapturedCall(method, body_offloaded, body_size, duration_seconds)
        )

    def cleaner_run(self, deleted: int) -> None:
        self.cleaner_runs.append(deleted)

    def schema_inference(self, *, status: str) -> None:
        self.schema_inference_calls.append(status)

    def schema_enqueue_failed(self) -> None:
        self.schema_enqueue_failed_count += 1

    def replay_attempt(self, *, status: str) -> None:
        self.replay_attempt_calls.append(status)

    def ssrf_block(self, *, reason: str) -> None:
        self.ssrf_block_calls.append(reason)

    def ssrf_dns_validation(self, *, result: str) -> None:
        self.ssrf_dns_validation_calls.append(result)
