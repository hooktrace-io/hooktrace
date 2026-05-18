from collections.abc import AsyncIterator
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from opentelemetry import metrics
from opentelemetry.metrics import UpDownCounter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from webhook_inspector.application.use_cases.list_requests import EndpointNotFoundError
from webhook_inspector.infrastructure.notifications.postgres_notifier import PostgresNotifier
from webhook_inspector.infrastructure.repositories.endpoint_repository import (
    PostgresEndpointRepository,
)
from webhook_inspector.infrastructure.repositories.request_repository import (
    PostgresRequestRepository,
)
from webhook_inspector.web.app.template_globals import apply_globals

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=select_autoescape())
apply_globals(_env)


def _sse_counter() -> UpDownCounter:
    return metrics.get_meter("webhook-inspector-app").create_up_down_counter(
        "webhook_inspector.sse.active_connections",
        description="Open SSE streams.",
    )


# Module-level singleton; built lazily on first access.
_active_sse: UpDownCounter | None = None


def _get_active_sse() -> UpDownCounter:
    global _active_sse
    if _active_sse is None:
        _active_sse = _sse_counter()
    return _active_sse


async def stream_for_token(
    token: str,
    session_factory: async_sessionmaker[AsyncSession],
    notifier: PostgresNotifier,
    hook_url: str = "",
) -> AsyncIterator[str]:
    async with session_factory() as session:
        endpoint = await PostgresEndpointRepository(session).find_by_token(token)
    if endpoint is None:
        raise EndpointNotFoundError(token)

    active = _get_active_sse()
    active.add(1)
    try:
        yield ": connected\n\n"

        fragment = _env.get_template("request_fragment.html")
        async for request_id in notifier.subscribe(endpoint.id):
            async with session_factory() as session:
                req = await PostgresRequestRepository(session).find_by_id(request_id)
            if req is None:
                continue
            html = fragment.render(
                req={
                    "id": str(req.id),
                    "method": req.method,
                    "path": req.path,
                    "body_size": req.body_size,
                    "received_at": req.received_at.isoformat(),
                    "headers": req.headers,
                    "body_preview": req.body_preview,
                    "signature_status": req.signature_status,
                    "detected_integration": req.detected_integration,
                    "detected_event_type": req.detected_event_type,
                },
                hook_url=hook_url,
            )
            # SSE multi-line data: one "data:" per line
            encoded = "\n".join(f"data: {line}" for line in html.splitlines())
            yield f"event: message\n{encoded}\n\n"
    finally:
        active.add(-1)
