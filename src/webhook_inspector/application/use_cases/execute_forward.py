"""Worker-invoked use case. arq calls execute_forward(ctx, forward_id_str);
this module contains the dataclass-based ExecuteForward use case.

Flow per attempt:
  1. Atomically claim the row (status → in_flight, attempt_count += 1).
     If claim fails (already in_flight/done/dead), log and exit — duplicate
     fire from arq retry or operator action.
  2. Load endpoint + request (fetch body from R2 if blob_key set).
  3. SSRF-validate the target URL.
  4. Build outbound: headers fusion + HMAC signature + Idempotency-Key.
  5. POST via HttpReplayTarget (PR4 component, reused).
  6. Decide next state via forward_decision.decide().
  7. record_outcome on the repo.
  8. If next_status == 'failed': re-enqueue with defer_seconds via ForwardQueue.

The method returns None on success path and on duplicate-fire (silent
skip). It catches ALL exceptions at the network/SSRF boundary and converts
them to record_outcome failure to keep arq from retrying via max_tries —
Model B owns the retry budget.
"""

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from webhook_inspector.application.use_cases.forward_decision import decide
from webhook_inspector.application.use_cases.outbound_signature import sign_forward
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


@dataclass
class ExecuteForward:
    endpoint_repo: EndpointRepository
    request_repo: RequestRepository
    forward_repo: ForwardRepository
    forward_queue: ForwardQueue
    target: HttpReplayTarget  # reuses PR4's SafeReplayTarget
    blob_storage: BlobStorage
    metrics: MetricsCollector
    secrets_key: bytes  # 32 bytes, from Settings

    async def execute(self, forward_id: UUID) -> None:
        now = datetime.now(UTC)

        claimed = await self.forward_repo.claim_for_attempt(forward_id, now=now)
        if claimed is None:
            # Already in_flight / succeeded / dead. Duplicate fire from arq
            # retry, or operator hand-enqueue. Not an error — log and exit.
            logger.info(
                "forward_skip_not_claimable",
                extra={"forward_id": str(forward_id)},
            )
            self.metrics.forward_attempt(status="skipped")
            return

        endpoint = await self.endpoint_repo.find_by_id(claimed.endpoint_id)
        captured = await self.request_repo.find_by_id(claimed.request_id)
        if endpoint is None or captured is None:
            # Endpoint or request got cleaned (TTL). Mark dead, do not re-enqueue.
            await self.forward_repo.record_outcome(
                forward_id,
                next_status="dead",
                final_status_code=None,
                final_error="endpoint or request not found (TTL expired?)",
                next_attempt_at=None,
                now=datetime.now(UTC),
            )
            self.metrics.forward_attempt(status="dead")
            return

        # Body: inline or R2-offloaded (same pattern as PR4 ReplayRequest).
        if captured.blob_key is not None:
            body = await self.blob_storage.get(captured.blob_key) or b""
        else:
            body = (captured.body_preview or "").encode("utf-8")

        # Headers fusion: stripped captured + endpoint config (endpoint wins on conflict).
        outbound_headers: dict[str, str] = {
            k: v
            for k, v in captured.headers.items()
            if k.lower() not in HEADERS_TO_STRIP_FROM_CAPTURED
        }
        if endpoint.forward_headers:
            outbound_headers.update(endpoint.forward_headers)

        # Idempotency key: stable per forward, advances per attempt so retries
        # don't look like duplicate requests to idempotent targets.
        outbound_headers["Idempotency-Key"] = f"{forward_id}:{claimed.attempt_count}"
        outbound_headers["X-Hooktrace-Forward-Id"] = str(forward_id)

        # HMAC signature if endpoint configured a secret.
        if endpoint.forward_secret_encrypted:
            secret_str = decrypt_secret(self.secrets_key, endpoint.forward_secret_encrypted)
            ts = int(time.time())
            sig = sign_forward(secret=secret_str.encode("utf-8"), timestamp=ts, body=body)
            outbound_headers["X-Hooktrace-Signature"] = f"t={ts},v1={sig}"

        # SSRF + HTTP.
        http_status: int | None = None
        network_error = False
        final_error: str | None = None
        try:
            validated = self.target.validate(claimed.target_url)
            status_code, _resp_headers, _resp_body = await self.target.send(
                method=captured.method,
                validated=validated,
                headers=outbound_headers,
                body=body,
            )
            http_status = status_code
        except SsrfBlockedError as e:
            # SSRF block is non-retryable — there's no future state where this
            # URL becomes safe. Mark dead immediately.
            await self.forward_repo.record_outcome(
                forward_id,
                next_status="dead",
                final_status_code=None,
                final_error=f"SsrfBlockedError: {e}",
                next_attempt_at=None,
                now=datetime.now(UTC),
            )
            self.metrics.forward_attempt(status="ssrf_blocked")
            return
        except HttpRequestFailedError as e:
            # Adapter has already translated httpx / OSError into a
            # port-level exception; the application layer just records the
            # failure and lets `decide()` handle the retry classification.
            network_error = True
            final_error = str(e)

        decision = decide(
            attempt_count=claimed.attempt_count,
            http_status=http_status,
            network_error=network_error,
        )

        next_attempt_at = (
            datetime.now(UTC).replace(microsecond=0) if decision.next_status == "failed" else None
        )
        await self.forward_repo.record_outcome(
            forward_id,
            next_status=decision.next_status,
            final_status_code=http_status,
            final_error=final_error,
            next_attempt_at=next_attempt_at,
            now=datetime.now(UTC),
        )

        if decision.next_status == "failed":
            await self.forward_queue.enqueue(forward_id, defer_seconds=decision.defer_seconds)

        self.metrics.forward_attempt(status=decision.next_status)
