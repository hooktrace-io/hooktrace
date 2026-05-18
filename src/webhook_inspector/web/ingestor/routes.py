import asyncio
import logging
import uuid
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry import trace
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from webhook_inspector.application.use_cases.capture_request import (
    CaptureRequest,
    EndpointNotFoundError,
)
from webhook_inspector.config import Settings
from webhook_inspector.domain.ports.metrics_collector import MetricsCollector
from webhook_inspector.domain.ports.schema_queue import SchemaQueue
from webhook_inspector.infrastructure.repositories.request_repository import (
    PostgresRequestRepository,
)
from webhook_inspector.observability.tracing import get_summary_processor
from webhook_inspector.web.ingestor.deps import (
    _blob_storage,
    get_capture_request,
    get_metrics,
    get_schema_queue,
    get_session,
    get_settings,
)

logger = logging.getLogger(__name__)


async def _safe_enqueue(
    queue: SchemaQueue,
    request_id: UUID,
    endpoint_id: UUID,
    integration: str,
    event_type: str | None,
    metrics: MetricsCollector,
) -> None:
    """Background-task wrapper around SchemaQueue.enqueue. Best-effort :
    a failed enqueue must not crash the response (which has already been
    sent by the time this runs). Logs + increments the failure counter.
    """
    try:
        await queue.enqueue(
            request_id,
            endpoint_id=endpoint_id,
            integration=integration,
            event_type=event_type,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "schema_enqueue_failed",
            extra={"request_id": str(request_id), "error": str(e)},
        )
        metrics.schema_enqueue_failed()


router = APIRouter()


@router.get("/health")
async def healthz(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> JSONResponse:
    """Deep health check: pings DB and verifies blob storage is reachable."""
    checks: dict[str, str] = {}
    overall_ok = True

    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["database"] = f"error: {type(e).__name__}"
        overall_ok = False

    try:
        storage = _blob_storage()
        # Unique probe key per invocation: the ingestor SA has
        # roles/storage.objectCreator (write-only, no overwrite, no read), so
        # we must always CREATE — never UPDATE — and skip the readback. The
        # bucket's 7-day lifecycle rule garbage-collects accumulated probes.
        probe_key = f"_healthz_probe/{uuid.uuid4().hex}"
        await storage.put(probe_key, b"ok")
        checks["blob_storage"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["blob_storage"] = f"error: {type(e).__name__}"
        overall_ok = False

    return JSONResponse(
        status_code=200 if overall_ok else 503,
        content={
            "status": "healthy" if overall_ok else "unhealthy",
            "checks": checks,
        },
    )


@router.api_route(
    "/h/{token}{rest:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def capture(
    token: str,
    rest: str,
    request: Request,
    background_tasks: BackgroundTasks,
    use_case: CaptureRequest = Depends(get_capture_request),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    schema_queue: SchemaQueue = Depends(get_schema_queue),  # noqa: B008
    metrics: MetricsCollector = Depends(get_metrics),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Response:
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > settings.max_body_bytes:
        raise HTTPException(status_code=413, detail="payload too large")

    body = await request.body()
    if len(body) > settings.max_body_bytes:
        raise HTTPException(status_code=413, detail="payload too large")

    try:
        _captured, endpoint = await use_case.execute(
            token=token,
            method=request.method,
            path=f"/h/{token}{rest}",
            query_string=request.url.query or None,
            headers={k.lower(): v for k, v in request.headers.items()},
            body=body,
            source_ip=request.client.host if request.client else "0.0.0.0",
        )
    except EndpointNotFoundError as e:
        raise HTTPException(status_code=404, detail="endpoint not found") from e

    # Attach trace summary in the same session as the INSERT — commit is atomic.
    current_span = trace.get_current_span()
    trace_id_int = current_span.get_span_context().trace_id
    trace_id_hex = format(trace_id_int, "032x") if trace_id_int else None
    summary = get_summary_processor().pop_summary(trace_id_hex) if trace_id_hex else []
    # Diagnostic for prod (raw stdout bypassing structlog).
    import sys

    sys.stdout.write(
        f"DIAG_TRACE_ATTACH trace_id={trace_id_hex} summary_len={len(summary)} "
        f"buffered={list(get_summary_processor()._buffer.keys())[:5]}\n"
    )
    sys.stdout.flush()
    if summary:
        await PostgresRequestRepository(session).update_trace_summary(_captured.id, summary)

    if _captured.detected_integration is not None:
        background_tasks.add_task(
            _safe_enqueue,
            schema_queue,
            _captured.id,
            endpoint.id,
            _captured.detected_integration,
            _captured.detected_event_type,
            metrics,
        )

    if endpoint.response_delay_ms > 0:
        await asyncio.sleep(endpoint.response_delay_ms / 1000)

    return Response(
        content=endpoint.response_body,
        status_code=endpoint.response_status_code,
        headers=endpoint.response_headers or None,
    )
