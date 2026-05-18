import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from webhook_inspector.application.use_cases.create_endpoint import CreateEndpoint
from webhook_inspector.application.use_cases.export_requests import (
    ExportRequests,
    ExportTooLargeError,
)
from webhook_inspector.application.use_cases.list_requests import (
    EndpointNotFoundError,
    ListRequests,
)
from webhook_inspector.application.use_cases.update_endpoint_config import UpdateEndpointConfig
from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.domain.entities.endpoint import (
    DEFAULT_RESPONSE_BODY,
    DEFAULT_RESPONSE_DELAY_MS,
    DEFAULT_RESPONSE_STATUS_CODE,
)
from webhook_inspector.domain.exceptions import EndpointValidationError, SlugAlreadyTakenError
from webhook_inspector.infrastructure.notifications.postgres_notifier import PostgresNotifier
from webhook_inspector.web.app.deps import (
    _session_factory,
    get_create_endpoint,
    get_export_requests,
    get_list_requests,
    get_notifier,
    get_session,
    get_update_endpoint_config,
)
from webhook_inspector.web.app.schemas.endpoint_config import EndpointConfigPatch
from webhook_inspector.web.app.sse import stream_for_token

router = APIRouter()


@router.get("/health")
async def healthz(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> JSONResponse:
    """Deep health check: pings the database with SELECT 1.

    Returns 200 + {status: healthy} when all checks pass.
    Returns 503 + {status: unhealthy, checks: {...}} otherwise.
    """
    checks: dict[str, str] = {}
    overall_ok = True

    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["database"] = f"error: {type(e).__name__}"
        overall_ok = False

    return JSONResponse(
        status_code=200 if overall_ok else 503,
        content={
            "status": "healthy" if overall_ok else "unhealthy",
            "checks": checks,
        },
    )


def hook_base_url(request: Request) -> str:
    """Derive the ingestor base URL from the app base URL.

    Cases handled (in priority order):
    1. Prod subdomain:    https://app.<domain>          → https://hook.<domain>
    2. Cloud Run default: https://*-app-*.a.run.app     → https://*-ingestor-*.a.run.app
    3. Local compose:     http://localhost:8000         → http://localhost:8001
    4. Fallback:          unchanged (single-host dev, e.g. http://test/)
    """
    base = str(request.base_url).rstrip("/")

    if "://app." in base:
        return base.replace("://app.", "://hook.")

    if re.search(r"webhook-inspector-app(-[a-z0-9]+)?-([a-z0-9]+)\.a\.run\.app", base):
        return base.replace("webhook-inspector-app", "webhook-inspector-ingestor")

    if ":8000" in base:
        return base.replace(":8000", ":8001")

    return base


class CustomResponseSpec(BaseModel):
    status_code: int = DEFAULT_RESPONSE_STATUS_CODE
    body: str = DEFAULT_RESPONSE_BODY
    headers: dict[str, str] = Field(default_factory=dict)
    delay_ms: int = DEFAULT_RESPONSE_DELAY_MS


class CreateEndpointRequest(BaseModel):
    response: CustomResponseSpec | None = None
    slug: str | None = None


class CreateEndpointResponse(BaseModel):
    url: str
    expires_at: str
    token: str
    response: CustomResponseSpec


@router.post("/api/endpoints", status_code=201, response_model=CreateEndpointResponse)
async def create_endpoint(
    request: Request,
    use_case: CreateEndpoint = Depends(get_create_endpoint),  # noqa: B008
    payload: Annotated[CreateEndpointRequest | None, Body()] = None,
) -> CreateEndpointResponse:
    response_spec = (payload.response if payload else None) or CustomResponseSpec()
    slug = payload.slug if payload else None
    try:
        endpoint = await use_case.execute(
            slug=slug,
            response_status_code=response_spec.status_code,
            response_body=response_spec.body,
            response_headers=response_spec.headers,
            response_delay_ms=response_spec.delay_ms,
        )
    except SlugAlreadyTakenError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except EndpointValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return CreateEndpointResponse(
        url=f"{hook_base_url(request)}/h/{endpoint.token}",
        expires_at=endpoint.expires_at.isoformat(),
        token=endpoint.token,
        response=CustomResponseSpec(
            status_code=endpoint.response_status_code,
            body=endpoint.response_body,
            headers=endpoint.response_headers,
            delay_ms=endpoint.response_delay_ms,
        ),
    )


@router.patch("/api/endpoints/{token}/config", status_code=204)
async def update_config(
    token: str,
    body: EndpointConfigPatch,
    use_case: UpdateEndpointConfig = Depends(get_update_endpoint_config),  # noqa: B008
) -> None:
    try:
        await use_case.execute(
            token=token,
            signature_provider=body.signature.provider if body.signature else None,
            signature_secret=body.signature.secret if body.signature else None,
        )
    except EndpointNotFoundError as e:
        raise HTTPException(status_code=404, detail="endpoint not found") from e


class RequestItem(BaseModel):
    id: UUID
    method: str
    path: str
    headers: dict[str, str]
    body_preview: str | None
    body_size: int
    received_at: str
    signature_status: str | None = None


class RequestList(BaseModel):
    items: list[RequestItem]
    next_before_id: UUID | None


async def _fetch_requests_or_raise(
    *,
    token: str,
    limit: int,
    before_id: UUID | None,
    q: str | None,
    use_case: ListRequests,
) -> list[CapturedRequest]:
    """Validate q length and fetch captured requests; raise HTTP errors on failure."""
    if q is not None and len(q) > 200:
        raise HTTPException(status_code=400, detail="q must be <= 200 characters")
    try:
        return await use_case.execute(token=token, limit=limit, before_id=before_id, q=q)
    except EndpointNotFoundError as e:
        raise HTTPException(status_code=404, detail="endpoint not found") from e


@router.get("/api/endpoints/{token}/requests", response_model=RequestList)
async def list_requests(
    token: str,
    limit: int = 50,
    before_id: UUID | None = None,
    q: str | None = None,
    use_case: ListRequests = Depends(get_list_requests),  # noqa: B008
) -> RequestList:
    items = await _fetch_requests_or_raise(
        token=token, limit=limit, before_id=before_id, q=q, use_case=use_case
    )

    return RequestList(
        items=[
            RequestItem(
                id=r.id,
                method=r.method,
                path=r.path,
                headers=r.headers,
                body_preview=r.body_preview,
                body_size=r.body_size,
                received_at=r.received_at.isoformat(),
                signature_status=r.signature_status,
            )
            for r in items
        ],
        next_before_id=items[-1].id if len(items) == limit else None,
    )


@router.get("/api/endpoints/{token}/requests.fragment", response_class=HTMLResponse)
async def list_requests_fragment(
    token: str,
    request: Request,
    limit: int = 50,
    before_id: UUID | None = None,
    q: str | None = None,
    use_case: ListRequests = Depends(get_list_requests),  # noqa: B008
) -> HTMLResponse:
    """Return rendered <li> rows for HTMX-driven search.

    Used by the viewer's search input. Reuses request_fragment.html so the markup
    matches what SSE pushes for live updates.
    """
    items = await _fetch_requests_or_raise(
        token=token, limit=limit, before_id=before_id, q=q, use_case=use_case
    )

    templates = request.app.state.templates
    hook_url = f"{hook_base_url(request)}/h/{token}"
    fragment_template = templates.env.get_template("request_fragment.html")
    rendered = "".join(
        fragment_template.render(
            req={
                "method": r.method,
                "path": r.path,
                "body_size": r.body_size,
                "received_at": r.received_at.isoformat(),
                "headers": r.headers,
                "body_preview": r.body_preview,
                "signature_status": r.signature_status,
            },
            hook_url=hook_url,
        )
        for r in items
    )
    return HTMLResponse(content=rendered)


@router.get("/api/endpoints/{token}/export.json")
async def export_endpoint(
    token: str,
    use_case: ExportRequests = Depends(get_export_requests),  # noqa: B008
) -> StreamingResponse:
    stream = use_case.execute(token=token)

    # Probe the stream so 404 / 413 surface BEFORE the StreamingResponse opens.
    # The async generator only raises on first __anext__.
    try:
        first = await stream.__anext__()
    except EndpointNotFoundError as e:
        raise HTTPException(status_code=404, detail="endpoint not found") from e
    except ExportTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e

    async def merged() -> AsyncIterator[bytes]:
        yield first
        async for chunk in stream:
            yield chunk

    filename = f"webhook-inspector-{token}-{datetime.now(UTC):%Y%m%d}.json"
    return StreamingResponse(
        merged(),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/stream/{token}")
async def sse_stream(
    token: str,
    request: Request,
    notifier: PostgresNotifier = Depends(get_notifier),  # noqa: B008
) -> StreamingResponse:
    try:
        hook_url = f"{hook_base_url(request)}/h/{token}"
        gen = stream_for_token(token, _session_factory(), notifier, hook_url)
        # Probe to surface 404 before opening stream
        first = await gen.__anext__()
    except EndpointNotFoundError as e:
        raise HTTPException(status_code=404, detail="endpoint not found") from e

    async def merged() -> AsyncIterator[str]:
        yield first
        async for chunk in gen:
            yield chunk

    return StreamingResponse(
        merged(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    return cast(
        HTMLResponse,
        templates.TemplateResponse(request=request, name="landing.html", context={}),
    )


@router.get("/{token}", response_class=HTMLResponse)
async def viewer(
    token: str,
    request: Request,
    use_case: ListRequests = Depends(get_list_requests),  # noqa: B008
) -> HTMLResponse:
    try:
        initial = await use_case.execute(token=token, limit=50)
    except EndpointNotFoundError as e:
        raise HTTPException(status_code=404, detail="endpoint not found") from e

    templates = request.app.state.templates
    return cast(
        HTMLResponse,
        templates.TemplateResponse(
            request=request,
            name="viewer.html",
            context={
                "token": token,
                "hook_url": f"{hook_base_url(request)}/h/{token}",
                "initial_requests": [
                    {
                        "method": r.method,
                        "path": r.path,
                        "body_size": r.body_size,
                        "received_at": r.received_at.isoformat(),
                        "headers": r.headers,
                        "body_preview": r.body_preview,
                        "signature_status": r.signature_status,
                    }
                    for r in initial
                ],
            },
        ),
    )
