"""Worker-invoked use case. arq calls execute_forward(ctx, forward_id_str);
this module contains the dataclass-based ExecuteForward use case.

Three-phase split to release the DB connection during HTTP I/O:

  Phase 1 (TX1 — claim):
    Open a session via ``unit_of_work``, atomically claim the row
    (status → in_flight, attempt_count += 1), load endpoint + captured
    request + body, commit + close. If claim returns None (duplicate
    fire), log and exit.

  Phase 2 (no DB):
    SSRF-validate the target URL, build outbound headers (fusion + HMAC
    + idempotency), POST. Catches SSRF/network errors and records them
    locally — does NOT touch the DB.

  Phase 3 (TX2 — record):
    Open a fresh session via ``unit_of_work``, record_outcome, commit +
    close.

  Phase 4 (post-commit, no DB):
    If next_status == 'failed': re-enqueue via ForwardQueue with the
    decided defer.

Why: under load the worker previously held ONE session open for the
whole attempt (~10s during slow forwards). The pool stayed empty even
though ~95% of the time was spent waiting on httpx. Splitting frees the
connection during HTTP, so a small pool can drive much higher concurrent
forward throughput.

``unit_of_work`` is the seam tests use to inject Fake repos: it's an
async context manager yielding a tuple of (forward_repo, endpoint_repo,
request_repo). Production passes a factory backed by SQLAlchemy +
Postgres*Repository; tests pass one backed by Fake*Repository.

The method returns None on success path and on duplicate-fire (silent
skip). Network/SSRF errors are recorded in TX2 — arq's max_tries is
never used; Model B owns the retry budget via record_outcome +
defer-seconds re-enqueue.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from webhook_inspector.application.use_cases.forward_decision import decide
from webhook_inspector.application.use_cases.outbound_signature import sign_forward
from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.entities.forward import Forward
from webhook_inspector.domain.ports.blob_storage import BlobStorage
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository
from webhook_inspector.domain.ports.forward_queue import ForwardQueue
from webhook_inspector.domain.ports.forward_repository import ForwardRepository
from webhook_inspector.domain.ports.http_replay_target import (
    HttpReplayTarget,
    HttpRequestFailedError,
    SsrfBlockedError,
)
from webhook_inspector.domain.ports.metrics_collector import MetricsCollector
from webhook_inspector.domain.ports.request_repository import RequestRepository
from webhook_inspector.domain.services.forwarded_headers import HEADERS_TO_STRIP_FROM_CAPTURED
from webhook_inspector.infrastructure.crypto.secrets import decrypt_secret

logger = logging.getLogger(__name__)


# Type alias for the unit-of-work seam.
# A UnitOfWork is an async context manager yielding the three
# session-bound repositories needed by the worker. The use case opens
# one per phase (claim, record) so each phase's TX is short.
ForwardUnitOfWork = AbstractAsyncContextManager[
    tuple[ForwardRepository, EndpointRepository, RequestRepository]
]
ForwardUnitOfWorkFactory = Callable[[], ForwardUnitOfWork]


@dataclass(frozen=True)
class _ClaimedState:
    """Snapshot captured from TX1, carried through phase 2 (HTTP) without
    holding the DB session open."""

    forward: Forward
    endpoint: Endpoint
    captured: CapturedRequest
    body: bytes


@dataclass
class ExecuteForward:
    # unit_of_work replaces the previous bound repo trio. Each phase
    # opens its own short-lived session via this factory; the session
    # is closed (and DB connection returned to the pool) BEFORE any
    # network I/O. Tests can wrap this with a counter to assert the
    # architectural invariant "two sessions per attempt".
    unit_of_work: ForwardUnitOfWorkFactory
    forward_queue: ForwardQueue
    target: HttpReplayTarget  # reuses PR4's SafeReplayTarget
    blob_storage: BlobStorage
    metrics: MetricsCollector
    secrets_key: bytes  # 32 bytes, from Settings

    async def execute(self, *, forward_id: UUID) -> None:
        # Phase 1 — claim + load + body fetch. The session is closed
        # (and the DB connection returned to the pool) BEFORE the HTTP
        # call.
        state = await self._claim_phase(forward_id)
        if state is None:
            return

        # Phase 2 — HTTP (no DB session held).
        http_status, network_error, final_error, ssrf_error = await self._http_phase(state)

        if ssrf_error is not None:
            await self._record_phase(
                forward_id,
                next_status="dead",
                final_status_code=None,
                final_error=f"SsrfBlockedError: {ssrf_error}",
                next_attempt_at=None,
            )
            self.metrics.forward_attempt(status="ssrf_blocked")
            return

        decision = decide(
            attempt_count=state.forward.attempt_count,
            http_status=http_status,
            network_error=network_error,
        )
        next_attempt_at = (
            datetime.now(UTC).replace(microsecond=0) if decision.next_status == "failed" else None
        )

        # Phase 3 — record outcome (TX2).
        await self._record_phase(
            forward_id,
            next_status=decision.next_status,
            final_status_code=http_status,
            final_error=final_error,
            next_attempt_at=next_attempt_at,
        )

        # Phase 4 — post-commit re-enqueue (no DB).
        if decision.next_status == "failed":
            await self.forward_queue.enqueue(forward_id, defer_seconds=decision.defer_seconds)

        self.metrics.forward_attempt(status=decision.next_status)

    async def _claim_phase(self, forward_id: UUID) -> _ClaimedState | None:
        """Open TX1: claim, load endpoint + request + body, commit, close."""
        now = datetime.now(UTC)
        async with self.unit_of_work() as (forward_repo, endpoint_repo, request_repo):
            claimed = await forward_repo.claim_for_attempt(forward_id, now=now)
            if claimed is None:
                # Already in_flight / succeeded / dead — duplicate fire.
                logger.info(
                    "forward_skip_not_claimable",
                    extra={"forward_id": str(forward_id)},
                )
                self.metrics.forward_attempt(status="skipped")
                return None

            endpoint, captured = await asyncio.gather(
                endpoint_repo.find_by_id(claimed.endpoint_id),
                request_repo.find_by_id(claimed.request_id),
            )
            if endpoint is None or captured is None:
                # Endpoint or request got cleaned (TTL). Record dead in
                # the SAME transaction — no HTTP step needed.
                await forward_repo.record_outcome(
                    forward_id,
                    next_status="dead",
                    final_status_code=None,
                    final_error="endpoint or request not found (TTL expired?)",
                    next_attempt_at=None,
                    now=datetime.now(UTC),
                )
                self.metrics.forward_attempt(status="dead")
                return None

            # Body: inline or R2-offloaded. The R2 fetch is not a DB
            # call, but doing it here means callers don't re-open a
            # session for it.
            if captured.blob_key is not None:
                body = await self.blob_storage.get(captured.blob_key) or b""
            else:
                body = (captured.body_preview or "").encode("utf-8")

        return _ClaimedState(forward=claimed, endpoint=endpoint, captured=captured, body=body)

    async def _http_phase(
        self, state: _ClaimedState
    ) -> tuple[int | None, bool, str | None, str | None]:
        """No DB session held. Returns (http_status, network_error,
        final_error, ssrf_error_message).
        """
        outbound_headers: dict[str, str] = {
            k: v
            for k, v in state.captured.headers.items()
            if k.lower() not in HEADERS_TO_STRIP_FROM_CAPTURED
        }
        if state.endpoint.forward_headers:
            outbound_headers.update(state.endpoint.forward_headers)

        outbound_headers["Idempotency-Key"] = f"{state.forward.id}:{state.forward.attempt_count}"
        outbound_headers["X-Hooktrace-Forward-Id"] = str(state.forward.id)

        if state.endpoint.forward_secret_encrypted:
            secret_str = decrypt_secret(self.secrets_key, state.endpoint.forward_secret_encrypted)
            ts = int(time.time())
            sig = sign_forward(secret=secret_str.encode("utf-8"), timestamp=ts, body=state.body)
            outbound_headers["X-Hooktrace-Signature"] = f"t={ts},v1={sig}"

        try:
            validated = await self.target.validate(state.forward.target_url)
            status_code, _resp_headers, _resp_body = await self.target.send(
                method=state.captured.method,
                validated=validated,
                headers=outbound_headers,
                body=state.body,
            )
            return status_code, False, None, None
        except SsrfBlockedError as e:
            return None, False, None, str(e)
        except HttpRequestFailedError as e:
            return None, True, str(e), None

    async def _record_phase(
        self,
        forward_id: UUID,
        *,
        next_status: str,
        final_status_code: int | None,
        final_error: str | None,
        next_attempt_at: datetime | None,
    ) -> None:
        """Open TX2: record_outcome, commit, close."""
        async with self.unit_of_work() as (forward_repo, _endpoint_repo, _request_repo):
            await forward_repo.record_outcome(
                forward_id,
                next_status=next_status,  # type: ignore[arg-type]
                final_status_code=final_status_code,
                final_error=final_error,
                next_attempt_at=next_attempt_at,
                now=datetime.now(UTC),
            )


__all__ = [
    "ExecuteForward",
    "ForwardUnitOfWork",
    "ForwardUnitOfWorkFactory",
]


# Convenience function for callers that want a typed AsyncIterator-style
# helper. NOT used internally — exists so tests + worker have a single
# `unit_of_work` shape that matches the type alias above.
async def _unit_of_work_iter() -> AsyncIterator[
    tuple[ForwardRepository, EndpointRepository, RequestRepository]
]:  # pragma: no cover - illustrative, not invoked
    raise NotImplementedError
