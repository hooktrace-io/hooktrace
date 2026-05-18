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
