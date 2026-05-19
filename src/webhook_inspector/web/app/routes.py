import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from typing import Annotated, Literal, cast
from uuid import UUID

import markdown
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from webhook_inspector.application.use_cases.abandon_forward import (
    AbandonForward,
    ForwardNotFoundError,
)
from webhook_inspector.application.use_cases.create_endpoint import CreateEndpoint
from webhook_inspector.application.use_cases.export_requests import (
    ExportRequests,
    ExportTooLargeError,
)
from webhook_inspector.application.use_cases.get_endpoint import GetEndpoint
from webhook_inspector.application.use_cases.get_forward_stats import GetForwardStats
from webhook_inspector.application.use_cases.list_forwards import ListForwards
from webhook_inspector.application.use_cases.list_integrations import ListIntegrations
from webhook_inspector.application.use_cases.list_requests import (
    EndpointNotFoundError,
    ListRequests,
)
from webhook_inspector.application.use_cases.redrive_pending_forwards import (
    RedrivePendingForwards,
)
from webhook_inspector.application.use_cases.replay_request import (
    ReplayPayloadTooLargeError,
    ReplayRequest,
    RequestNotFoundError,
)
from webhook_inspector.application.use_cases.retry_forward import (
    ForwardNotRetryableError,
    RetryForward,
)
from webhook_inspector.application.use_cases.update_endpoint_config import UpdateEndpointConfig
from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.domain.entities.endpoint import (
    DEFAULT_RESPONSE_BODY,
    DEFAULT_RESPONSE_DELAY_MS,
    DEFAULT_RESPONSE_STATUS_CODE,
)
from webhook_inspector.domain.entities.forward import Forward
from webhook_inspector.domain.exceptions import EndpointValidationError, SlugAlreadyTakenError
from webhook_inspector.domain.services.hmac.base import ValidationResult
from webhook_inspector.infrastructure.notifications.postgres_notifier import PostgresNotifier
from webhook_inspector.web.app.deps import (
    _session_factory,
    get_abandon_forward,
    get_create_endpoint,
    get_endpoint_use_case,
    get_export_requests,
    get_forward_stats_use_case,
    get_list_forwards,
    get_list_integrations,
    get_list_requests,
    get_metrics,
    get_notifier,
    get_redrive_pending_forwards,
    get_replay_request,
    get_retry_forward,
    get_session,
    get_update_endpoint_config,
)
from webhook_inspector.web.app.schemas.endpoint_config import EndpointConfigPatch
from webhook_inspector.web.app.sse import stream_for_token
from webhook_inspector.web.middleware.token_rate_limit import enforce_token_limit

# --- Module-level constants ---------------------------------------------------
# Per-token cap on outbound replays. IP-keyed middleware already protects the
# API surface against scraping; this caps the blast radius of a single hijacked
# token used to spam targets.
REPLAY_LIMIT_PER_HOUR = 10

# Window used by all hourly per-token caps (replay, capture, ...).
RATE_LIMIT_WINDOW_SECONDS_1H = 3600

# Max length of the `q` search query on /requests endpoints. Bounded so the
# server doesn't run unbounded LIKE/regex over user-controlled strings.
SEARCH_QUERY_MAX_CHARS = 200

# Pagination bounds for list_forwards (and other listing endpoints that share
# the same shape).
LIST_LIMIT_MIN = 1
LIST_LIMIT_MAX = 200


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
            forward_url=str(body.forward.url) if body.forward else None,
            forward_headers=body.forward.headers if body.forward else None,
            forward_secret=body.forward.secret if body.forward else None,
        )
    except EndpointNotFoundError as e:
        raise HTTPException(status_code=404, detail="endpoint not found") from e


class IntegrationAggregateResponse(BaseModel):
    integration: Literal[
        "stripe",
        "github",
        "shopify",
        "twilio",
        "mailgun",
        "discord",
        "slack",
        "zapier",
        "n8n",
    ]
    total: int
    event_types: dict[str, int]
    signature_status_counts: dict[str, int]


@router.get(
    "/api/endpoints/{token}/integrations",
    response_model=list[IntegrationAggregateResponse],
)
async def list_integrations_route(
    token: str,
    use_case: ListIntegrations = Depends(get_list_integrations),  # noqa: B008
) -> list[IntegrationAggregateResponse]:
    try:
        aggregates = await use_case.execute_for_token(token)
    except EndpointNotFoundError as e:
        raise HTTPException(status_code=404, detail="endpoint not found") from e
    return [
        IntegrationAggregateResponse(
            integration=a.integration,
            total=a.total,
            event_types=a.event_types,
            signature_status_counts=a.signature_status_counts,
        )
        for a in aggregates
    ]


class RequestItem(BaseModel):
    id: UUID
    method: str
    path: str
    headers: dict[str, str]
    body_preview: str | None
    body_size: int
    received_at: str
    signature_status: (
        Literal[
            ValidationResult.VALID,
            ValidationResult.INVALID,
            ValidationResult.MISSING,
            ValidationResult.NO_PROVIDER,
        ]
        | None
    ) = None
    detected_integration: (
        Literal[
            "stripe",
            "github",
            "shopify",
            "twilio",
            "mailgun",
            "discord",
            "slack",
            "zapier",
            "n8n",
        ]
        | None
    ) = None
    detected_event_type: str | None = None


class RequestList(BaseModel):
    items: list[RequestItem]
    next_before_id: UUID | None


class ForwardItem(BaseModel):
    id: UUID
    request_id: UUID
    target_url: str
    status: str
    attempt_count: int
    last_attempt_at: str | None
    next_attempt_at: str | None
    final_status_code: int | None
    final_error: str | None
    manual_retry_at: str | None
    created_at: str


class ForwardList(BaseModel):
    items: list[ForwardItem]
    next_before_id: UUID | None


class ForwardStatsResponse(BaseModel):
    pending: int = 0
    in_flight: int = 0
    succeeded: int = 0
    failed: int = 0
    dead: int = 0
    abandoned: int = 0


def _to_forward_item(forward: Forward) -> ForwardItem:
    return ForwardItem(
        id=forward.id,
        request_id=forward.request_id,
        target_url=forward.target_url,
        status=forward.status,
        attempt_count=forward.attempt_count,
        last_attempt_at=(forward.last_attempt_at.isoformat() if forward.last_attempt_at else None),
        next_attempt_at=(forward.next_attempt_at.isoformat() if forward.next_attempt_at else None),
        final_status_code=forward.final_status_code,
        final_error=forward.final_error,
        manual_retry_at=(forward.manual_retry_at.isoformat() if forward.manual_retry_at else None),
        created_at=forward.created_at.isoformat(),
    )


async def _fetch_requests_or_raise(
    *,
    token: str,
    limit: int,
    before_id: UUID | None,
    q: str | None,
    use_case: ListRequests,
) -> list[CapturedRequest]:
    """Validate q length and fetch captured requests; raise HTTP errors on failure."""
    if q is not None and len(q) > SEARCH_QUERY_MAX_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"q must be <= {SEARCH_QUERY_MAX_CHARS} characters",
        )
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
                detected_integration=r.detected_integration,
                detected_event_type=r.detected_event_type,
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
                "id": str(r.id),
                "method": r.method,
                "path": r.path,
                "body_size": r.body_size,
                "received_at": r.received_at.isoformat(),
                "headers": r.headers,
                "body_preview": r.body_preview,
                "signature_status": r.signature_status,
                "detected_integration": r.detected_integration,
                "detected_event_type": r.detected_event_type,
            },
            hook_url=hook_url,
        )
        for r in items
    )
    return HTMLResponse(content=rendered)


class ReplayBody(BaseModel):
    target_url: HttpUrl  # Pydantic validates http(s) at the boundary; SSRF guard does the rest
    include_headers: bool = True
    include_body: bool = True


class ReplayResponse(BaseModel):
    id: UUID
    status_code: int | None
    error: str | None
    duration_ms: int
    attempted_at: str


@router.post(
    "/api/endpoints/{token}/requests/{request_id}/replay",
    response_model=ReplayResponse,
    status_code=200,
)
async def replay_request_route(
    token: str,
    request_id: UUID,
    body: ReplayBody,
    use_case: ReplayRequest = Depends(get_replay_request),  # noqa: B008
) -> ReplayResponse:
    # Per-token cap (10/h): IP-keyed middleware already protects the API
    # surface against high-volume scraping; the token-keyed cap limits the
    # blast radius of a single hijacked token used to spam targets.
    await enforce_token_limit(
        token=token,
        rule_name="replay",
        limit=REPLAY_LIMIT_PER_HOUR,
        window_seconds=RATE_LIMIT_WINDOW_SECONDS_1H,
        metrics=get_metrics(),
    )
    try:
        replay = await use_case.execute(
            token=token,
            request_id=request_id,
            target_url=str(body.target_url),
            include_headers=body.include_headers,
            include_body=body.include_body,
        )
    except EndpointNotFoundError as e:
        raise HTTPException(status_code=404, detail="endpoint not found") from e
    except RequestNotFoundError as e:
        # Same 404 regardless of "endpoint missing" vs "wrong owner" — leaking
        # the difference would let an attacker probe request_ids across tokens.
        raise HTTPException(status_code=404, detail="request not found") from e
    except ReplayPayloadTooLargeError as e:
        raise HTTPException(status_code=413, detail="payload too large") from e

    return ReplayResponse(
        id=replay.id,
        status_code=replay.status_code,
        error=replay.error,
        duration_ms=replay.duration_ms,
        attempted_at=replay.attempted_at.isoformat(),
    )


@router.get("/api/endpoints/{token}/forwards", response_model=ForwardList)
async def list_forwards_route(
    token: str,
    status: Annotated[list[str] | None, Query()] = None,
    limit: int = 50,
    before_id: UUID | None = None,
    use_case: ListForwards = Depends(get_list_forwards),  # noqa: B008
) -> ForwardList:
    if not LIST_LIMIT_MIN <= limit <= LIST_LIMIT_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"limit must be in [{LIST_LIMIT_MIN}, {LIST_LIMIT_MAX}]",
        )
    try:
        forwards = await use_case.execute(
            token=token, statuses=status, limit=limit, before_id=before_id
        )
    except EndpointNotFoundError as e:
        raise HTTPException(status_code=404, detail="endpoint not found") from e

    items = [_to_forward_item(f) for f in forwards]
    return ForwardList(
        items=items,
        next_before_id=items[-1].id if len(items) == limit else None,
    )


@router.get("/api/endpoints/{token}/forwards/stats", response_model=ForwardStatsResponse)
async def get_forward_stats_route(
    token: str,
    use_case: GetForwardStats = Depends(get_forward_stats_use_case),  # noqa: B008
) -> ForwardStatsResponse:
    try:
        counts = await use_case.execute(token=token)
    except EndpointNotFoundError as e:
        raise HTTPException(status_code=404, detail="endpoint not found") from e
    return ForwardStatsResponse(**counts)


@router.post(
    "/api/endpoints/{token}/forwards/{forward_id}/retry",
    response_model=ForwardItem,
)
async def retry_forward_route(
    token: str,
    forward_id: UUID,
    use_case: RetryForward = Depends(get_retry_forward),  # noqa: B008
) -> ForwardItem:
    try:
        forward = await use_case.execute(token=token, forward_id=forward_id)
    except EndpointNotFoundError as e:
        raise HTTPException(status_code=404, detail="endpoint not found") from e
    except ForwardNotRetryableError as e:
        # 404 (not 403) AND a generic detail string — the same response is
        # returned for "wrong status" and "cross-endpoint" so the API cannot be
        # used to probe whether a forward_id exists under a different token.
        raise HTTPException(status_code=404, detail="forward not retryable") from e
    return _to_forward_item(forward)


@router.post("/api/endpoints/{token}/forwards/redrive")
async def redrive_forwards_route(
    token: str,
    use_case: RedrivePendingForwards = Depends(get_redrive_pending_forwards),  # noqa: B008
) -> dict[str, int]:
    try:
        count = await use_case.execute(token=token)
    except EndpointNotFoundError as e:
        raise HTTPException(status_code=404, detail="endpoint not found") from e
    return {"redriven": count}


@router.delete(
    "/api/endpoints/{token}/forwards/{forward_id}",
    response_model=ForwardItem,
)
async def abandon_forward_route(
    token: str,
    forward_id: UUID,
    use_case: AbandonForward = Depends(get_abandon_forward),  # noqa: B008
) -> ForwardItem:
    try:
        forward = await use_case.execute(token=token, forward_id=forward_id)
    except EndpointNotFoundError as e:
        raise HTTPException(status_code=404, detail="endpoint not found") from e
    except ForwardNotFoundError as e:
        raise HTTPException(status_code=404, detail="forward not found") from e
    return _to_forward_item(forward)


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


@router.get("/tos", response_class=HTMLResponse)
async def tos_view(request: Request) -> HTMLResponse:
    """Minimal Terms of Service. Static HTML — no auth, no DB read.

    Declared BEFORE the catch-all `/{token}` route below so FastAPI matches
    the literal path first ; `/tos` would otherwise be parsed as a token
    and 404'd by the viewer.
    """
    templates = request.app.state.templates
    return cast(
        HTMLResponse,
        templates.TemplateResponse(request=request, name="tos.html", context={}),
    )


# Public integration docs (PR13). The .md files ship inside the package
# (src/webhook_inspector/docs/integrations/) so the path resolves identically
# in editable dev installs and in production wheel installs — no Dockerfile
# tweak or env var needed.
_DOCS_ROOT: Path = Path(__file__).resolve().parent.parent.parent / "docs" / "integrations"

# Closed allowlist for slug → file. Validated against this frozen set BEFORE
# any filesystem access — never path-joined directly with user input. Adding
# a new service requires editing both this set and the matching .md file.
_ALLOWED_DOCS: frozenset[str] = frozenset(
    {
        "stripe",
        "github",
        "shopify",
        "twilio",
        "mailgun",
        "discord",
        "slack",
        "zapier",
        "n8n",
        "verifying-forwards",
    }
)

_DOC_TITLES: dict[str, str] = {
    "stripe": "Stripe webhooks",
    "github": "GitHub webhooks",
    "shopify": "Shopify webhooks",
    "twilio": "Twilio webhooks",
    "mailgun": "Mailgun webhooks",
    "discord": "Discord webhooks",
    "slack": "Slack webhooks",
    "zapier": "Zapier webhooks",
    "n8n": "n8n webhooks",
    "verifying-forwards": "Verifying hooktrace forwards",
}


def _render_doc_markdown(md_path: Path) -> str:
    """Render a markdown file to HTML. Pure I/O + markdown call.

    The `markdown` package is untyped (no published stubs at the version we
    pin), so `markdown.markdown(...)` is `Any`. Cast to str at the boundary
    to keep callers strictly typed without sprinkling `cast(..)` further up.
    """
    text_md = md_path.read_text(encoding="utf-8")
    return cast(str, markdown.markdown(text_md, extensions=["fenced_code", "tables"]))


@router.get("/docs/integrations", response_class=HTMLResponse)
async def docs_integrations_index(request: Request) -> HTMLResponse:
    """Public landing for the integration guides. Renders README.md."""
    index_path = _DOCS_ROOT / "README.md"
    rendered = _render_doc_markdown(index_path)
    templates = request.app.state.templates
    return cast(
        HTMLResponse,
        templates.TemplateResponse(
            request=request,
            name="docs.html",
            context={
                "title": "Integration guides",
                "description": (
                    "HMAC signature schemes hooktrace validates for Stripe, GitHub, "
                    "Shopify, Twilio, Mailgun, Discord, Slack, Zapier, n8n, plus the "
                    "verifying-forwards public contract."
                ),
                "slug": None,
                "content": rendered,
            },
        ),
    )


@router.get("/docs/integrations/{slug}", response_class=HTMLResponse)
async def docs_integration_page(slug: str, request: Request) -> HTMLResponse:
    """Render one integration doc by slug.

    The slug is validated against `_ALLOWED_DOCS` — a frozen set of the 10
    known doc names. Anything outside the set returns 404. We never join the
    raw slug onto a filesystem path, so path traversal (`../etc/passwd`,
    URL-encoded `..`, etc.) cannot escape `_DOCS_ROOT`.
    """
    if slug not in _ALLOWED_DOCS:
        raise HTTPException(status_code=404, detail="doc not found")

    md_path = _DOCS_ROOT / f"{slug}.md"
    rendered = _render_doc_markdown(md_path)

    title = _DOC_TITLES.get(slug, slug)
    templates = request.app.state.templates
    return cast(
        HTMLResponse,
        templates.TemplateResponse(
            request=request,
            name="docs.html",
            context={
                "title": title,
                "description": f"{title} — what hooktrace validates and where to find the secret.",
                "slug": slug,
                "content": rendered,
            },
        ),
    )


@router.get("/{token}/integrations", response_class=HTMLResponse)
async def integrations_view(
    token: str,
    request: Request,
    use_case: ListIntegrations = Depends(get_list_integrations),  # noqa: B008
) -> HTMLResponse:
    try:
        aggregates = await use_case.execute_for_token(token)
    except EndpointNotFoundError as e:
        raise HTTPException(status_code=404, detail="endpoint not found") from e

    templates = request.app.state.templates
    return cast(
        HTMLResponse,
        templates.TemplateResponse(
            request=request,
            name="integrations.html",
            context={"token": token, "aggregates": aggregates},
        ),
    )


@router.get("/{token}/forwards", response_class=HTMLResponse)
async def forwards_view(
    token: str,
    request: Request,
    status: Annotated[list[str] | None, Query()] = None,
    list_use_case: ListForwards = Depends(get_list_forwards),  # noqa: B008
    stats_use_case: GetForwardStats = Depends(get_forward_stats_use_case),  # noqa: B008
) -> HTMLResponse:
    # Default filter when no ?status param: show actionable rows only
    # (pending / in_flight / failed / dead) — succeeded + abandoned are hidden.
    effective_statuses = status if status else ["pending", "in_flight", "failed", "dead"]
    try:
        forwards = await list_use_case.execute(
            token=token,
            statuses=effective_statuses,
            limit=50,
            before_id=None,
        )
        stats = await stats_use_case.execute(token=token)
    except EndpointNotFoundError as e:
        raise HTTPException(status_code=404, detail="endpoint not found") from e

    templates = request.app.state.templates
    return cast(
        HTMLResponse,
        templates.TemplateResponse(
            request=request,
            name="forwards.html",
            context={
                "token": token,
                "forwards": [_to_forward_item(f).model_dump(mode="json") for f in forwards],
                "stats": stats,
                "active_filters": effective_statuses,
            },
        ),
    )


@router.get("/{token}", response_class=HTMLResponse)
async def viewer(
    token: str,
    request: Request,
    use_case: ListRequests = Depends(get_list_requests),  # noqa: B008
    get_endpoint: GetEndpoint = Depends(get_endpoint_use_case),  # noqa: B008
) -> HTMLResponse:
    try:
        endpoint = await get_endpoint.execute(token=token)
        initial = await use_case.execute(token=token, limit=50)
    except EndpointNotFoundError as e:
        raise HTTPException(status_code=404, detail="endpoint not found") from e

    # Countdown badge: ceil so 23h59 still reads "1 day", clamp to 0 so an
    # expires_at in the past (cleaner hasn't run yet) shows "Expires today"
    # rather than a negative number. Postgres TIMESTAMP WITHOUT TIME ZONE
    # returns a naive datetime ; we treat stored values as UTC (matches what
    # CreateEndpoint persists via datetime.now(UTC)).
    expires_at = endpoint.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    delta = expires_at - datetime.now(UTC)
    days_until_expiry = max(0, ceil(delta.total_seconds() / 86400))

    templates = request.app.state.templates
    return cast(
        HTMLResponse,
        templates.TemplateResponse(
            request=request,
            name="viewer.html",
            context={
                "token": token,
                "hook_url": f"{hook_base_url(request)}/h/{token}",
                "days_until_expiry": days_until_expiry,
                "initial_requests": [
                    {
                        "id": str(r.id),
                        "method": r.method,
                        "path": r.path,
                        "body_size": r.body_size,
                        "received_at": r.received_at.isoformat(),
                        "headers": r.headers,
                        "body_preview": r.body_preview,
                        "signature_status": r.signature_status,
                        "detected_integration": r.detected_integration,
                        "detected_event_type": r.detected_event_type,
                    }
                    for r in initial
                ],
            },
        ),
    )
