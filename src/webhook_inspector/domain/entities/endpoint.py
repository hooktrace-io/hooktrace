from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from webhook_inspector.domain.exceptions import (
    ForbiddenResponseHeaderError,
    InvalidResponseDelayError,
    InvalidResponseStatusError,
    ResponseBodyTooLargeError,
)

DEFAULT_RESPONSE_STATUS_CODE = 200
DEFAULT_RESPONSE_BODY = '{"ok":true}'
DEFAULT_RESPONSE_DELAY_MS = 0
_MAX_BODY_BYTES = 65_536  # implementation detail, keep private
_FORBIDDEN_HEADERS = frozenset(
    ["content-length", "transfer-encoding", "connection", "host", "date"]
)


@dataclass(slots=True)
class Endpoint:
    id: UUID
    token: str
    created_at: datetime
    expires_at: datetime
    request_count: int = 0
    response_status_code: int = DEFAULT_RESPONSE_STATUS_CODE
    response_body: str = DEFAULT_RESPONSE_BODY
    response_headers: dict[str, str] = field(default_factory=dict)
    response_delay_ms: int = DEFAULT_RESPONSE_DELAY_MS
    signature_provider: str | None = None
    signature_secret_encrypted: bytes | None = None

    # V3 — forward config (Block 1 of PR7+PR8)
    forward_url: str | None = None
    forward_headers: dict[str, str] | None = None
    forward_secret_encrypted: bytes | None = None

    @classmethod
    def create(
        cls,
        token: str,
        ttl_days: int,
        *,
        response_status_code: int = DEFAULT_RESPONSE_STATUS_CODE,
        response_body: str = DEFAULT_RESPONSE_BODY,
        response_headers: dict[str, str] | None = None,
        response_delay_ms: int = DEFAULT_RESPONSE_DELAY_MS,
    ) -> "Endpoint":
        if ttl_days <= 0:
            raise ValueError("ttl_days must be positive")

        _validate_response_status(response_status_code)
        _validate_response_delay(response_delay_ms)
        _validate_response_body_size(response_body)
        headers = response_headers or {}
        _validate_response_headers(headers)

        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            token=token,
            created_at=now,
            expires_at=now + timedelta(days=ttl_days),
            request_count=0,
            response_status_code=response_status_code,
            response_body=response_body,
            response_headers=headers,
            response_delay_ms=response_delay_ms,
        )

    def is_expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at


def _validate_response_status(code: int) -> None:
    if not 100 <= code <= 599:
        raise InvalidResponseStatusError(f"response_status_code must be in [100, 599], got {code}")


def _validate_response_delay(delay_ms: int) -> None:
    if not 0 <= delay_ms <= 30_000:
        raise InvalidResponseDelayError(f"response_delay_ms must be in [0, 30000], got {delay_ms}")


def _validate_response_body_size(body: str) -> None:
    if len(body.encode("utf-8")) > _MAX_BODY_BYTES:
        raise ResponseBodyTooLargeError(f"response_body exceeds {_MAX_BODY_BYTES} bytes")


def _validate_response_headers(headers: dict[str, str]) -> None:
    for name in headers:
        if name.lower() in _FORBIDDEN_HEADERS:
            raise ForbiddenResponseHeaderError(
                f"header '{name}' is reserved and cannot be overridden"
            )
