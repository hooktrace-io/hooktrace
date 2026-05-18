"""Unit tests for ReplayBody / ReplayResponse Pydantic schemas.

No database, no Docker — pure schema validation.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from webhook_inspector.domain.entities.replay import Replay
from webhook_inspector.web.app.routes import ReplayBody, ReplayResponse


def test_replay_body_accepts_https_url():
    rb = ReplayBody(target_url="https://example.com")  # type: ignore[arg-type]
    assert str(rb.target_url) == "https://example.com/"


def test_replay_body_rejects_non_http_scheme():
    with pytest.raises(ValidationError):
        ReplayBody(target_url="file:///etc/passwd")  # type: ignore[arg-type]


def test_replay_body_defaults_include_headers_true_include_body_true():
    rb = ReplayBody(target_url="https://example.com/hook")  # type: ignore[arg-type]
    assert rb.include_headers is True
    assert rb.include_body is True


def test_replay_response_serializes_required_fields():
    request_id = uuid4()
    now = datetime.now(UTC)
    replay = Replay.success(
        request_id=request_id,
        target_url="https://example.com/hook",
        status_code=201,
        body_preview="ok",
        headers={"content-type": "application/json"},
        duration_ms=42,
        now=now,
    )
    rr = ReplayResponse(
        id=replay.id,
        status_code=replay.status_code,
        error=replay.error,
        duration_ms=replay.duration_ms,
        attempted_at=replay.attempted_at.isoformat(),
    )
    assert isinstance(rr.id, UUID)
    assert rr.status_code == 201
    assert rr.error is None
    assert rr.duration_ms == 42
    assert rr.attempted_at == now.isoformat()
