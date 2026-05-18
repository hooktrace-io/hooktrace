"""Worker-invoked use case. arq calls update_inferred_schema(ctx,
request_id_str) ; the use case loads the captured request, fetches the
body (from R2 if offloaded), infers the schema, diffs against the
cumulative schema for (endpoint, integration, event_type), updates the
request's schema_drift column, and upserts the cumulative schema.

Concurrency model: arq delivers EVERY enqueued job (per-request _job_id,
no dedup). Serialization over the shared cumulative schema row is done
INSIDE this use case via Postgres pg_advisory_xact_lock keyed on
(endpoint_id, integration, event_type) — released automatically on
transaction commit. Two concurrent workers for the same event-class
block on the lock until the other completes.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from webhook_inspector.domain.entities.inferred_schema import InferredSchema
from webhook_inspector.domain.ports.blob_storage import BlobStorage
from webhook_inspector.domain.ports.metrics_collector import MetricsCollector
from webhook_inspector.domain.ports.request_repository import RequestRepository
from webhook_inspector.domain.ports.schema_repository import SchemaRepository
from webhook_inspector.domain.services.schema_inferer import diff_schemas, infer_schema


@dataclass
class UpdateInferredSchema:
    request_repo: RequestRepository
    schema_repo: SchemaRepository
    blob_storage: BlobStorage
    metrics: MetricsCollector

    async def execute(self, request_id: UUID) -> None:
        captured = await self.request_repo.find_by_id(request_id)
        if captured is None or captured.detected_integration is None:
            # Request was cleaned (TTL) or has no detected integration —
            # nothing to infer. Not an error.
            self.metrics.schema_inference(status="skipped_no_integration")
            return

        # Body: inline or R2-offloaded (PR4 / PR7 pattern).
        if captured.blob_key is not None:
            body = await self.blob_storage.get(captured.blob_key) or b""
        else:
            body = (captured.body_preview or "").encode("utf-8")

        body_schema = infer_schema(body)
        if not body_schema:
            # Non-JSON body, oversized, or non-dict root — can't infer.
            self.metrics.schema_inference(status="skipped_non_json")
            return

        # CRITICAL: acquire Postgres advisory lock BEFORE the find/diff/merge/upsert
        # sequence. Per-request _job_id means arq delivers every job (no dedup),
        # so two concurrent captures of the same event-class can race on the
        # cumulative schema row. The lock is keyed on (endpoint, integration,
        # event_type) — released automatically on transaction commit. Bounded
        # by arq's job_timeout=120s in WorkerSettings (PR6).
        lock_key = _advisory_lock_key(
            captured.endpoint_id,
            captured.detected_integration,
            captured.detected_event_type,
        )
        await self.schema_repo.acquire_advisory_lock(lock_key)
        # From here until the implicit transaction commit at function exit,
        # we hold the lock — no other worker can interleave on the same key.

        # Step 1: load existing cumulative schema BEFORE updating it.
        # Drift is computed against this baseline.
        old = await self.schema_repo.find_by_key(
            endpoint_id=captured.endpoint_id,
            integration=captured.detected_integration,
            event_type=captured.detected_event_type,
        )
        old_schema = old.schema_json if old else {}

        drift = diff_schemas(old_schema, body_schema)
        had_new_fields = bool(drift["added"]) or bool(drift["changed"])

        # Step 2: persist the drift on the request row.
        await self.request_repo.update_schema_drift(request_id, drift)

        # Step 3: merge the new sample into the cumulative schema and upsert.
        # genson handles the merge via add_schema on its SchemaBuilder ; we
        # can simply build a fresh builder seeded with both schemas.
        merged = _merge_schemas(old_schema, body_schema)
        now = datetime.now(UTC)
        upserted = InferredSchema(
            id=old.id if old else uuid4(),
            endpoint_id=captured.endpoint_id,
            integration=captured.detected_integration,
            event_type=captured.detected_event_type,
            schema_json=merged,
            sample_count=(old.sample_count if old else 0) + 1,
            version=(old.version if old else 0) + 1,
            last_field_added_at=now
            if had_new_fields
            else (old.last_field_added_at if old else None),
            created_at=old.created_at if old else now,
            updated_at=now,
        )
        await self.schema_repo.upsert_with_version(upserted)

        self.metrics.schema_inference(
            status="updated" if had_new_fields else "no_drift",
        )


def _advisory_lock_key(endpoint_id: UUID, integration: str, event_type: str | None) -> int:
    """Stable signed-int64 hash of the (endpoint, integration, event_type) tuple.
    Postgres pg_advisory_xact_lock takes a bigint, so we coerce SHA-256's first
    8 bytes into the signed int64 range.
    """
    h = hashlib.sha256(f"{endpoint_id}:{integration}:{event_type or ''}".encode()).digest()
    raw = int.from_bytes(h[:8], "big", signed=False)
    return raw - (1 << 64) if raw >= (1 << 63) else raw


def _merge_schemas(old: dict[str, object], new: dict[str, object]) -> dict[str, object]:
    """Merge two JSON Schemas into a unified one via genson. If either is
    empty, return the other (no-op).
    """
    if not old:
        return new
    if not new:
        return old
    from genson import SchemaBuilder

    builder = SchemaBuilder()
    builder.add_schema(old)
    builder.add_schema(new)
    result: dict[str, object] = builder.to_schema()
    return result
