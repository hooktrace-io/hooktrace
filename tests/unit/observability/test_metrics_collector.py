from opentelemetry.metrics import MeterProvider
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from webhook_inspector.infrastructure.observability.otel_metrics_collector import (
    OtelMetricsCollector,
)


def _build_collector() -> tuple[OtelMetricsCollector, InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    provider: MeterProvider = SdkMeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test")
    collector = OtelMetricsCollector(meter)
    return collector, reader


def _metric_data_points(reader: InMemoryMetricReader, name: str):
    metrics = reader.get_metrics_data()
    for rm in metrics.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == name:
                    return list(m.data.data_points)
    return []


def test_endpoint_created_increments_counter():
    collector, reader = _build_collector()
    collector.endpoint_created()
    collector.endpoint_created()
    points = _metric_data_points(reader, "webhook_inspector.endpoints.created")
    assert sum(p.value for p in points) == 2


def test_request_captured_records_with_labels():
    collector, reader = _build_collector()
    collector.request_captured(
        method="POST", body_offloaded=False, body_size=100, duration_seconds=0.05
    )
    captured = _metric_data_points(reader, "webhook_inspector.requests.captured")
    assert any(
        p.attributes.get("method") == "POST"
        and p.attributes.get("body_offloaded") is False
        and p.value == 1
        for p in captured
    )
    body_size = _metric_data_points(reader, "webhook_inspector.requests.body_size_bytes")
    assert any(p.sum == 100 for p in body_size)
    duration = _metric_data_points(reader, "webhook_inspector.requests.capture_duration_seconds")
    # No labels on duration — just verify the value was recorded.
    assert any(p.sum == 0.05 for p in duration)


def test_cleaner_run_emits_heartbeat_and_deletions():
    collector, reader = _build_collector()
    collector.cleaner_run(deleted=3)
    runs = _metric_data_points(reader, "webhook_inspector.cleaner.runs.completed")
    deletions = _metric_data_points(reader, "webhook_inspector.cleaner.deletions")
    assert sum(p.value for p in runs) == 1
    assert sum(p.value for p in deletions) == 3


def test_cleaner_run_with_zero_deletions_still_emits_heartbeat():
    collector, reader = _build_collector()
    collector.cleaner_run(deleted=0)
    runs = _metric_data_points(reader, "webhook_inspector.cleaner.runs.completed")
    assert sum(p.value for p in runs) == 1


def test_rate_limit_block_records_rule_and_reason_labels():
    collector, reader = _build_collector()
    collector.rate_limit_block(rule="ingest", reason="quota")
    collector.rate_limit_block(rule="ingest", reason="fail_closed")
    points = _metric_data_points(reader, "webhook_inspector.rate_limit.block_total")
    by_label = {(p.attributes.get("rule"), p.attributes.get("reason")): p.value for p in points}
    assert by_label.get(("ingest", "quota")) == 1
    assert by_label.get(("ingest", "fail_closed")) == 1


def test_rate_limit_redis_error_records_rule_label():
    collector, reader = _build_collector()
    collector.rate_limit_redis_error(rule="api")
    collector.rate_limit_redis_error(rule="api")
    points = _metric_data_points(reader, "webhook_inspector.rate_limit.redis_error_total")
    assert sum(p.value for p in points if p.attributes.get("rule") == "api") == 2
