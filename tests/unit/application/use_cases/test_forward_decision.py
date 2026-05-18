"""Truth-table tests for the forward retry decision matrix.

Pure function — no I/O. Each case asserts the next_status + defer_seconds
the worker should apply after a given attempt outcome.
"""

import pytest

from webhook_inspector.application.use_cases.forward_decision import decide
from webhook_inspector.domain.entities.forward import MAX_ATTEMPTS, RETRY_BACKOFFS

# --- 2xx happy path ---


@pytest.mark.parametrize("status", [200, 201, 202, 204, 299])
def test_2xx_succeeded(status: int) -> None:
    d = decide(attempt_count=1, http_status=status, network_error=False)
    assert d.next_status == "succeeded"
    assert d.defer_seconds == 0


# --- 4xx hard failures ---


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 410, 422, 451])
def test_4xx_non_retryable_dead_immediately(status: int) -> None:
    d = decide(attempt_count=1, http_status=status, network_error=False)
    assert d.next_status == "dead"
    assert d.defer_seconds == 0


# --- Retryable status codes ---


@pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503, 504, 507, 508])
def test_retryable_status_first_attempt_failed_with_first_backoff(
    status: int,
) -> None:
    d = decide(attempt_count=1, http_status=status, network_error=False)
    assert d.next_status == "failed"
    assert d.defer_seconds == RETRY_BACKOFFS[0]  # 30


# --- Network errors ---


def test_network_error_first_attempt_failed_with_first_backoff() -> None:
    d = decide(attempt_count=1, http_status=None, network_error=True)
    assert d.next_status == "failed"
    assert d.defer_seconds == RETRY_BACKOFFS[0]


def test_network_error_after_max_attempts_dead() -> None:
    d = decide(attempt_count=MAX_ATTEMPTS, http_status=None, network_error=True)
    assert d.next_status == "dead"
    assert d.defer_seconds == 0


# --- Backoff schedule progression ---


def test_backoff_schedule_progression_through_5_attempts() -> None:
    """Attempts 1..4 → failed with backoffs [30, 120, 600, 3600]; attempt 5 → dead."""
    expected = [
        (1, "failed", 30),
        (2, "failed", 120),
        (3, "failed", 600),
        (4, "failed", 3600),
        (5, "dead", 0),
    ]
    for attempt_count, want_status, want_defer in expected:
        d = decide(attempt_count=attempt_count, http_status=503, network_error=False)
        assert d.next_status == want_status, (
            f"attempt={attempt_count} expected {want_status}, got {d.next_status}"
        )
        assert d.defer_seconds == want_defer, (
            f"attempt={attempt_count} expected {want_defer}s, got {d.defer_seconds}s"
        )


def test_5_attempts_then_dead_even_on_retryable_status() -> None:
    d = decide(attempt_count=MAX_ATTEMPTS, http_status=503, network_error=False)
    assert d.next_status == "dead"
    assert d.defer_seconds == 0


def test_4xx_at_max_attempts_still_dead() -> None:
    """Non-retryable status at any attempt → dead, no special max-attempt path."""
    d = decide(attempt_count=MAX_ATTEMPTS, http_status=404, network_error=False)
    assert d.next_status == "dead"


def test_4xx_at_first_attempt_dead_not_failed() -> None:
    """Confirms we don't 'fail then retry' on a 404 — straight to dead."""
    d = decide(attempt_count=1, http_status=404, network_error=False)
    assert d.next_status == "dead"
    assert d.defer_seconds == 0


# --- Defensive : edge cases ---


def test_2xx_at_max_attempts_still_succeeded() -> None:
    """A response that arrives on the LAST attempt should still count as success."""
    d = decide(attempt_count=MAX_ATTEMPTS, http_status=200, network_error=False)
    assert d.next_status == "succeeded"


def test_max_attempts_constant_is_5() -> None:
    """The retry budget is 5. If this changes, the schedule changes too."""
    assert MAX_ATTEMPTS == 5
    assert len(RETRY_BACKOFFS) == MAX_ATTEMPTS
