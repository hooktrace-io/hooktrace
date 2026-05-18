import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from webhook_inspector.application.use_cases.capture_request import (
    CaptureRequest,
    EndpointNotFoundError,
)
from webhook_inspector.config import Settings
from webhook_inspector.web.ingestor.deps import (
    _blob_storage,
    get_capture_request,
    get_session,
    get_settings,
)

logger = logging.getLogger(__name__)


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
    use_case: CaptureRequest = Depends(get_capture_request),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
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

    if endpoint.response_delay_ms > 0:
        await asyncio.sleep(endpoint.response_delay_ms / 1000)

    return Response(
        content=endpoint.response_body,
        status_code=endpoint.response_status_code,
        headers=endpoint.response_headers or None,
    )
