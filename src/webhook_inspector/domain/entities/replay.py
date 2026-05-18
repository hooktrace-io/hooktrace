from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

REPLAY_RESPONSE_BODY_PREVIEW_BYTES = 4 * 1024  # 4 KB — matches DB column cap


@dataclass
class Replay:
    id: UUID
    request_id: UUID
    target_url: str
    status_code: int | None
    response_body_preview: str | None
    response_headers: dict[str, str] | None
    error: str | None
    duration_ms: int
    attempted_at: datetime

    @classmethod
    def success(
        cls,
        *,
        request_id: UUID,
        target_url: str,
        status_code: int,
        body_preview: str | None,
        headers: dict[str, str],
        duration_ms: int,
        now: datetime,
    ) -> "Replay":
        return cls(
            id=uuid4(),
            request_id=request_id,
            target_url=target_url,
            status_code=status_code,
            response_body_preview=body_preview,
            response_headers=headers,
            error=None,
            duration_ms=duration_ms,
            attempted_at=now,
        )

    @classmethod
    def failure(
        cls,
        *,
        request_id: UUID,
        target_url: str,
        error: str,
        duration_ms: int,
        now: datetime,
    ) -> "Replay":
        return cls(
            id=uuid4(),
            request_id=request_id,
            target_url=target_url,
            status_code=None,
            response_body_preview=None,
            response_headers=None,
            error=error,
            duration_ms=duration_ms,
            attempted_at=now,
        )
