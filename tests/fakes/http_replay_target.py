"""In-memory HttpReplayTarget for unit tests."""

from dataclasses import dataclass

from webhook_inspector.domain.ports.http_replay_target import (
    HttpReplayTarget,
    SsrfBlockedError,
    ValidatedTarget,
)


@dataclass
class ReplayCall:
    method: str
    validated: ValidatedTarget
    headers: dict[str, str]
    body: bytes


class FakeHttpReplayTarget(HttpReplayTarget):
    """In-memory HttpReplayTarget. .respond() and .raise_on_validate() /
    .raise_on_send() set up the next call's behavior.
    """

    def __init__(self) -> None:
        self.last_call: ReplayCall | None = None
        self._response: tuple[int, dict[str, str], bytes] = (200, {}, b"")
        self._validate_error: SsrfBlockedError | None = None
        self._send_error: Exception | None = None

    def respond(self, *, status: int, body: bytes, headers: dict[str, str]) -> None:
        self._response = (status, headers, body)

    def raise_on_validate(self, exc: SsrfBlockedError) -> None:
        self._validate_error = exc

    def raise_on_send(self, exc: Exception) -> None:
        self._send_error = exc

    def validate(self, url: str) -> ValidatedTarget:
        if self._validate_error is not None:
            raise self._validate_error
        return ValidatedTarget(url=url, host="example.com", port=443, ip="1.2.3.4")

    async def send(
        self,
        *,
        method: str,
        validated: ValidatedTarget,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], bytes]:
        self.last_call = ReplayCall(method=method, validated=validated, headers=headers, body=body)
        if self._send_error is not None:
            raise self._send_error
        return self._response
