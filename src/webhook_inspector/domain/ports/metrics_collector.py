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
    def schema_inference(self, *, status: str) -> None:
        """Increment schema_inference_total{status}.
        status values: updated | no_drift | skipped_no_integration | skipped_non_json
        """

    @abstractmethod
    def schema_enqueue_failed(self) -> None:
        """Increment schema_enqueue_failed_total. Emitted when Redis is down."""

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
