"""Replay a captured request to a target URL.

The use case authorizes via the endpoint token (the caller must own the
endpoint that captured the request), fetches the body (inline preview or
R2-offloaded), strips hop-by-hop and security-sensitive headers, runs the
SSRF guard, sends the HTTP call, and persists the outcome as a Replay row.

Three-phase split mirrors ExecuteForward (see that module for the
rationale): the DB connection is held only during authorization+lookup
(TX1) and outcome persistence (TX2). The HTTP step runs with no DB
session, freeing the pool during slow replays.

Every exit path emits a ``replay_attempt`` metric with a status label so
PR10's rate-limit tuning has the signal.
"""

import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.entities.replay import (
    REPLAY_RESPONSE_BODY_PREVIEW_BYTES,
    Replay,
)
from webhook_inspector.domain.exceptions import EndpointNotFoundError
from webhook_inspector.domain.ports.blob_storage import BlobStorage
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository
from webhook_inspector.domain.ports.http_replay_target import (
    HttpReplayTarget,
    HttpRequestFailedError,
    SsrfBlockedError,
)
from webhook_inspector.domain.ports.metrics_collector import MetricsCollector
from webhook_inspector.domain.ports.replay_repository import ReplayRepository
from webhook_inspector.domain.ports.request_repository import RequestRepository
from webhook_inspector.domain.services.forwarded_headers import HEADERS_TO_STRIP_FROM_CAPTURED

MAX_REPLAY_BODY_BYTES = 1 * 1024 * 1024


class RequestNotFoundError(Exception):
    pass


class ReplayPayloadTooLargeError(Exception):
    pass


# Type alias for ReplayRequest's unit-of-work seam (parallel to
# ExecuteForward.ForwardUnitOfWork). A ReplayUnitOfWork yields the three
# repos the use case needs.
ReplayUnitOfWork = AbstractAsyncContextManager[
    tuple[EndpointRepository, RequestRepository, ReplayRepository]
]
ReplayUnitOfWorkFactory = Callable[[], ReplayUnitOfWork]


@dataclass(frozen=True)
class _AuthorizedState:
    """Snapshot from TX1 carried through the HTTP phase."""

    endpoint: Endpoint
    captured: CapturedRequest
    body: bytes


@dataclass
class ReplayRequest:
    unit_of_work: ReplayUnitOfWorkFactory
    target: HttpReplayTarget
    blob_storage: BlobStorage
    metrics: MetricsCollector

    async def execute(
        self,
        *,
        token: str,
        request_id: UUID,
        target_url: str,
        include_headers: bool = True,
        include_body: bool = True,
    ) -> Replay:
        # Phase 1 — auth + load + body fetch (TX1).
        state = await self._authorize_phase(token=token, request_id=request_id)
        body = state.body if include_body else b""
        if len(body) > MAX_REPLAY_BODY_BYTES:
            self.metrics.replay_attempt(status="payload_too_large")
            raise ReplayPayloadTooLargeError(f"body {len(body)} > {MAX_REPLAY_BODY_BYTES}")

        # Headers : strip hop-by-hop + auth + sender sig.
        headers = (
            {
                k: v
                for k, v in state.captured.headers.items()
                if k.lower() not in HEADERS_TO_STRIP_FROM_CAPTURED
            }
            if include_headers
            else {}
        )

        # Phase 2 — validate + HTTP (no DB session held).
        started = time.monotonic()
        now = datetime.now(UTC)
        try:
            validated = await self.target.validate(target_url)
        except SsrfBlockedError as e:
            replay = Replay.failure(
                request_id=request_id,
                target_url=target_url,
                error=f"SsrfBlockedError: {e}",
                duration_ms=int((time.monotonic() - started) * 1000),
                now=now,
            )
            await self._record_phase(replay)
            self.metrics.replay_attempt(status="ssrf_blocked")
            return replay

        try:
            status_code, resp_headers, resp_body = await self.target.send(
                method=state.captured.method,
                validated=validated,
                headers=headers,
                body=body,
            )
            body_preview = resp_body[:REPLAY_RESPONSE_BODY_PREVIEW_BYTES].decode(
                "utf-8", errors="replace"
            )
            replay = Replay.success(
                request_id=request_id,
                target_url=target_url,
                status_code=status_code,
                body_preview=body_preview,
                headers=resp_headers,
                duration_ms=int((time.monotonic() - started) * 1000),
                now=now,
            )
            metric_status = "success" if 200 <= status_code < 300 else "target_error"
        except HttpRequestFailedError as e:
            replay = Replay.failure(
                request_id=request_id,
                target_url=target_url,
                error=str(e),
                duration_ms=int((time.monotonic() - started) * 1000),
                now=now,
            )
            metric_status = "network_error"

        # Phase 3 — persist outcome (TX2).
        await self._record_phase(replay)
        self.metrics.replay_attempt(status=metric_status)
        return replay

    async def _authorize_phase(self, *, token: str, request_id: UUID) -> _AuthorizedState:
        """Open TX1: auth, load captured + body, commit, close."""
        async with self.unit_of_work() as (endpoint_repo, request_repo, _replay_repo):
            endpoint = await endpoint_repo.find_by_token(token)
            if endpoint is None:
                self.metrics.replay_attempt(status="endpoint_not_found")
                raise EndpointNotFoundError(token)

            captured = await request_repo.find_by_id(request_id)
            if captured is None or captured.endpoint_id != endpoint.id:
                self.metrics.replay_attempt(status="request_not_found")
                raise RequestNotFoundError(f"request {request_id} not owned by endpoint {token}")

            if captured.blob_key is not None:
                body = await self.blob_storage.get(captured.blob_key) or b""
            else:
                body = (captured.body_preview or "").encode("utf-8")

        return _AuthorizedState(endpoint=endpoint, captured=captured, body=body)

    async def _record_phase(self, replay: Replay) -> None:
        """Open TX2: persist the Replay row, commit, close."""
        async with self.unit_of_work() as (_endpoint_repo, _request_repo, replay_repo):
            await replay_repo.save(replay)


__all__ = [
    "MAX_REPLAY_BODY_BYTES",
    "ReplayPayloadTooLargeError",
    "ReplayRequest",
    "ReplayUnitOfWork",
    "ReplayUnitOfWorkFactory",
    "RequestNotFoundError",
]
