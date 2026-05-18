from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

ForwardStatus = Literal["pending", "in_flight", "succeeded", "failed", "dead"]

# Schedule for failed retries (seconds since last_attempt_at). 5 entries =
# 5 total attempts before status='dead'. Indexed by (attempt_count - 1).
RETRY_BACKOFFS = (30, 120, 600, 3600, 14400)  # 30s, 2m, 10m, 1h, 4h
MAX_ATTEMPTS = len(RETRY_BACKOFFS)  # 5


@dataclass
class Forward:
    id: UUID
    request_id: UUID
    endpoint_id: UUID
    target_url: str
    status: ForwardStatus
    attempt_count: int
    last_attempt_at: datetime | None
    next_attempt_at: datetime | None
    final_status_code: int | None
    final_error: str | None
    forward_started_at: datetime | None
    forward_completed_at: datetime | None
    created_at: datetime

    @classmethod
    def new(
        cls,
        *,
        request_id: UUID,
        endpoint_id: UUID,
        target_url: str,
        now: datetime,
    ) -> "Forward":
        return cls(
            id=uuid4(),
            request_id=request_id,
            endpoint_id=endpoint_id,
            target_url=target_url,
            status="pending",
            attempt_count=0,
            last_attempt_at=None,
            next_attempt_at=now,
            final_status_code=None,
            final_error=None,
            forward_started_at=None,
            forward_completed_at=None,
            created_at=now,
        )
