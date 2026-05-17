from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4


@dataclass(slots=True)
class CapturedRequest:
    id: UUID
    endpoint_id: UUID
    method: str
    path: str
    query_string: str | None
    headers: dict[str, str]
    body_preview: str | None
    body_size: int
    blob_key: str | None
    source_ip: str
    received_at: datetime
    signature_status: str | None = None

    @classmethod
    def create(
        cls,
        endpoint_id: UUID,
        method: str,
        path: str,
        query_string: str | None,
        headers: dict[str, str],
        body: bytes,
        source_ip: str,
        inline_threshold_bytes: int,
    ) -> "CapturedRequest":
        if method != method.upper():
            raise ValueError("method must be uppercase")

        request_id = uuid4()
        body_size = len(body)

        if body_size <= inline_threshold_bytes:
            preview = _decode_body_safe(body)
            blob_key = None
        else:
            preview = None
            blob_key = f"{endpoint_id}/{request_id}"

        return cls(
            id=request_id,
            endpoint_id=endpoint_id,
            method=method,
            path=path,
            query_string=query_string,
            headers=headers,
            body_preview=preview,
            body_size=body_size,
            blob_key=blob_key,
            source_ip=source_ip,
            received_at=datetime.now(UTC),
        )


def _decode_body_safe(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return repr(body)[2:-1]  # strip b'' wrapper
