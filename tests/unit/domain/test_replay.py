"""Unit tests for the Replay entity."""

from datetime import UTC, datetime
from uuid import uuid4

from webhook_inspector.domain.entities.replay import REPLAY_RESPONSE_BODY_PREVIEW_BYTES, Replay


def test_REPLAY_RESPONSE_BODY_PREVIEW_BYTES_is_4kb() -> None:  # noqa: N802
    assert REPLAY_RESPONSE_BODY_PREVIEW_BYTES == 4 * 1024


def test_success_factory_builds_replay_with_all_response_fields() -> None:
    request_id = uuid4()
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    headers = {"Content-Type": "application/json"}

    replay = Replay.success(
        request_id=request_id,
        target_url="https://example.com/hook",
        status_code=200,
        body_preview='{"ok":true}',
        headers=headers,
        duration_ms=123,
        now=now,
    )

    assert replay.request_id == request_id
    assert replay.target_url == "https://example.com/hook"
    assert replay.status_code == 200
    assert replay.response_body_preview == '{"ok":true}'
    assert replay.response_headers == headers
    assert replay.error is None
    assert replay.duration_ms == 123
    assert replay.attempted_at == now
    assert replay.id is not None


def test_failure_factory_builds_replay_with_error_only() -> None:
    request_id = uuid4()
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    replay = Replay.failure(
        request_id=request_id,
        target_url="https://example.com/hook",
        error="Connection refused",
        duration_ms=50,
        now=now,
    )

    assert replay.request_id == request_id
    assert replay.target_url == "https://example.com/hook"
    assert replay.status_code is None
    assert replay.response_body_preview is None
    assert replay.response_headers is None
    assert replay.error == "Connection refused"
    assert replay.duration_ms == 50
    assert replay.attempted_at == now
    assert replay.id is not None
