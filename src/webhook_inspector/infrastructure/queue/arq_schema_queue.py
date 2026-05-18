from uuid import UUID

from arq import ArqRedis

from webhook_inspector.domain.ports.schema_queue import SchemaQueue


class ArqSchemaQueue(SchemaQueue):
    """arq-backed SchemaQueue. Per-request jobs : _job_id = "schema:{request_id}"
    so EVERY captured request gets its own job (no inter-request dedup).
    Concurrency over the shared cumulative schema is serialized by a Postgres
    advisory lock keyed on (endpoint_id, integration, event_type) inside the
    UpdateInferredSchema use case — NOT by arq's dedup.

    Earlier design used _job_id keyed on (endpoint, integration, event_type).
    Wrong: arq dedupes identical _job_id values while queued, so a 2nd capture
    for the same event class while job 1 was queued got silently dropped — the
    2nd request_id never reached the worker, schema_drift stayed null. The
    promise "every captured request still gets drift" was false.

    Per-request jobs + advisory lock is the correct decoupling: arq guarantees
    delivery of every job; the advisory lock guarantees serialization at
    execute time.
    """

    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def enqueue(
        self,
        request_id: UUID,
        *,
        endpoint_id: UUID,  # noqa: ARG002
        integration: str,  # noqa: ARG002
        event_type: str | None,  # noqa: ARG002
    ) -> None:
        # _job_id keyed PER-REQUEST so we never collapse captures. The
        # endpoint/integration/event_type args are not needed for routing
        # (they're loaded from the request row in the worker), but they
        # remain in the port signature for caller readability.
        await self._pool.enqueue_job(
            "update_inferred_schema",
            str(request_id),
            _job_id=f"schema:{request_id}",
        )
