"""Port for application metrics emission. Adapter wires it to OpenTelemetry."""

from abc import ABC, abstractmethod


class MetricsCollector(ABC):
    @abstractmethod
    def endpoint_created(self) -> None:
        """Increment endpoints_created_total — one event per successful CreateEndpoint."""

    @abstractmethod
    def request_captured(
        self,
        *,
        method: str,
        body_offloaded: bool,
        body_size: int,
        duration_seconds: float,
    ) -> None:
        """Increment requests_captured_total{method, body_offloaded}
        + record body_size + duration_seconds histograms.
        Labels are strict cardinality — no user-controlled values.
        """

    @abstractmethod
    def cleaner_run(self, deleted: int) -> None:
        """Increment cleaner_runs_completed_total (heartbeat for absence alerts)
        + cleaner_deletions_total counter (only if deleted > 0).
        """

    @abstractmethod
    def replay_attempt(self, *, status: str) -> None:
        """Increment replay_attempt_total{status}.
        status values: success | target_error | network_error | ssrf_blocked |
                       endpoint_not_found | request_not_found | payload_too_large
        """

    @abstractmethod
    def ssrf_block(self, *, reason: str) -> None:
        """Increment ssrf_block_total{reason}.
        reason values: scheme | port | userinfo | host_suffix |
                       ip_literal_private | dns_resolved_private | dns_empty
        """

    @abstractmethod
    def ssrf_dns_validation(self, *, result: str) -> None:
        """Increment ssrf_dns_validation_total{result}.
        result values: ok | private | empty | nxdomain
        """

    @abstractmethod
    def forward_attempt(self, *, status: str) -> None:
        """Increment forward_attempt_total{status}.
        status values: succeeded | failed | dead | skipped | ssrf_blocked
        """

    @abstractmethod
    def forward_enqueue_failed(self) -> None:
        """Increment forward_enqueue_failed_total — best-effort enqueue from
        CaptureRequest failed (Redis down, etc.). The Forward row was saved
        but no worker job was scheduled; operator must re-enqueue manually.
        """

    @abstractmethod
    def rate_limit_block(self, *, rule: str, reason: str) -> None:
        """Increment rate_limit_block_total{rule, reason}.
        rule values: ingest | api | replay | capture
        reason values: quota | fail_closed
        """

    @abstractmethod
    def rate_limit_redis_error(self, *, rule: str) -> None:
        """Increment rate_limit_redis_error_total{rule}.
        Cardinality strict — rule label only, no IP/token/path.
        """
