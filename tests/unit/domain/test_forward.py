"""Unit tests for the Forward entity and module-level constants."""

from datetime import UTC, datetime
from uuid import uuid4

from webhook_inspector.domain.entities.forward import (
    MAX_ATTEMPTS,
    RETRY_BACKOFFS,
    Forward,
)


def _now() -> datetime:
    return datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)


def test_new_factory_returns_pending_status() -> None:
    fwd = Forward.new(
        request_id=uuid4(),
        endpoint_id=uuid4(),
        target_url="https://example.com/hook",
        now=_now(),
    )
    assert fwd.status == "pending"


def test_new_factory_sets_next_attempt_at_to_now() -> None:
    now = _now()
    fwd = Forward.new(
        request_id=uuid4(),
        endpoint_id=uuid4(),
        target_url="https://example.com/hook",
        now=now,
    )
    assert fwd.next_attempt_at == now


def test_new_factory_attempt_count_zero() -> None:
    fwd = Forward.new(
        request_id=uuid4(),
        endpoint_id=uuid4(),
        target_url="https://example.com/hook",
        now=_now(),
    )
    assert fwd.attempt_count == 0


def test_retry_backoffs_has_5_entries_matching_max_attempts() -> None:
    assert len(RETRY_BACKOFFS) == MAX_ATTEMPTS


def test_max_attempts_is_5() -> None:
    assert MAX_ATTEMPTS == 5
