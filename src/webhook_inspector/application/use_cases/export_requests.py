"""Stream JSON export of an endpoint's captured requests."""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.exceptions import EndpointNotFoundError
from webhook_inspector.domain.ports.blob_storage import BlobStorage
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository
from webhook_inspector.domain.ports.request_repository import RequestRepository


class ExportTooLargeError(Exception):
    """Endpoint has more captured requests than the configured export cap."""


@dataclass
class ExportRequests:
    endpoint_repo: EndpointRepository
    request_repo: RequestRepository
    blob_storage: BlobStorage
    max_requests: int

    async def execute(self, *, token: str) -> AsyncIterator[bytes]:
        endpoint = await self.endpoint_repo.find_by_token(token)
        if endpoint is None:
            raise EndpointNotFoundError(token)

        total = await self.request_repo.count_by_endpoint(endpoint.id)
        if total > self.max_requests:
            raise ExportTooLargeError(
                f"Export exceeds {self.max_requests} requests (filter then export will land in V3)."
            )

        async for chunk in self._stream(endpoint, total):
            yield chunk

    async def _stream(self, endpoint: Endpoint, total: int) -> AsyncIterator[bytes]:
        header: dict[str, Any] = {
            "endpoint": {
                "token": endpoint.token,
                "created_at": endpoint.created_at.isoformat(),
                "expires_at": endpoint.expires_at.isoformat(),
                "response": {
                    "status_code": endpoint.response_status_code,
                    "body": endpoint.response_body,
                    "headers": endpoint.response_headers,
                    "delay_ms": endpoint.response_delay_ms,
                },
            },
            "exported_at": datetime.now(UTC).isoformat(),
            "exported_request_count": total,
        }
        # Strip the trailing brace of the header and append the requests array.
        # This lets us stream the array row-by-row without holding all rows in
        # memory at once.
        header_dumped = json.dumps(header)
        # We rely on default json.dumps() ending the dict with `}`. Do NOT pass
        # indent= or separators= here — the slice [:-1] would then drop a newline
        # or whitespace instead of the closing brace.
        assert header_dumped.endswith("}"), "json.dumps(header) must end with '}'"
        prefix = header_dumped[:-1] + ',"requests":['
        yield prefix.encode()

        first = True
        async for req in self.request_repo.stream_for_export(
            endpoint_id=endpoint.id, max_count=self.max_requests
        ):
            if not first:
                yield b","
            first = False
            body = await self._resolve_body(req)
            yield json.dumps(_request_to_dict(req, body)).encode()

        yield b"]}"

    async def _resolve_body(self, req: CapturedRequest) -> str | None:
        if req.blob_key is None:
            return req.body_preview
        raw = await self.blob_storage.get(req.blob_key)
        if raw is None:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return repr(raw)[2:-1]


def _request_to_dict(req: CapturedRequest, body: str | None) -> dict[str, Any]:
    return {
        "id": str(req.id),
        "method": req.method,
        "path": req.path,
        "headers": req.headers,
        "body": body,
        "body_size": req.body_size,
        "received_at": req.received_at.isoformat(),
    }
