"""Port for application metrics emission. Adapter wires it to OpenTelemetry."""

from abc import ABC, abstractmethod


class MetricsCollector(ABC):
    @abstractmethod
    def endpoint_created(self) -> None: ...

    @abstractmethod
    def request_captured(
        self,
        *,
        method: str,
        body_offloaded: bool,
        body_size: int,
        duration_seconds: float,
    ) -> None: ...

    @abstractmethod
    def cleaner_run(self, deleted: int) -> None: ...

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
