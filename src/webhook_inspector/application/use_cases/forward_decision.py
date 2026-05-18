"""Retry decision for forward attempts.

Pure function — no I/O, no clock, no DB. Caller passes in the current state,
gets back the next state and the delay until the next attempt (or 0 if
terminal).
"""

from dataclasses import dataclass
from typing import Literal

from webhook_inspector.domain.entities.forward import MAX_ATTEMPTS, RETRY_BACKOFFS

# Status codes that warrant retry. Everything else 4xx → terminal failure.
# 408 Request Timeout, 425 Too Early, 429 Too Many Requests, 5xx all retryable.
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 507, 508})


@dataclass(frozen=True)
class ForwardDecision:
    next_status: Literal["succeeded", "failed", "dead"]
    defer_seconds: int  # 0 if terminal (succeeded or dead)


def decide(*, attempt_count: int, http_status: int | None, network_error: bool) -> ForwardDecision:
    """Decide what to do after a forward attempt.

    `attempt_count` is the number of attempts INCLUDING the one that just
    finished (1-indexed). `http_status` is None on network error.

    Returns the next state for the Forward row:
    - "succeeded" : 2xx response, no more attempts needed.
    - "dead" : terminal failure — either a non-retryable status (e.g. 404,
      422) or the retry budget is exhausted (attempt_count >= MAX_ATTEMPTS).
    - "failed" : transient failure, schedule another attempt after
      `defer_seconds` (from RETRY_BACKOFFS).
    """
    if not network_error and http_status is not None and 200 <= http_status < 300:
        return ForwardDecision(next_status="succeeded", defer_seconds=0)

    is_retryable = network_error or (http_status is not None and http_status in _RETRYABLE_STATUS)
    if not is_retryable:
        # Hard failure (4xx other than 408/425/429) — terminal, no retry.
        return ForwardDecision(next_status="dead", defer_seconds=0)

    if attempt_count >= MAX_ATTEMPTS:
        return ForwardDecision(next_status="dead", defer_seconds=0)

    # Schedule the next attempt. attempt_count is 1-indexed.
    return ForwardDecision(
        next_status="failed",
        defer_seconds=RETRY_BACKOFFS[attempt_count - 1],
    )
