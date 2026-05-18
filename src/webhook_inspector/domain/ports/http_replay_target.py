from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ValidatedTarget:
    """Result of HttpReplayTarget.validate(): the URL passed in + the IP
    httpx will (initially) connect to. See SafeReplayTarget docstring for
    the DNS rebinding caveat — V3 trusts httpx's connect-time resolution
    and only filters at validate-time.
    """

    url: str
    host: str
    port: int
    ip: str


class SsrfBlockedError(Exception):
    """Raised when a target URL is rejected by the SSRF guard."""


class HttpReplayTarget(ABC):
    """Abstraction over the network — lets the use case depend on a port,
    not on httpx directly. Tests substitute with a fake that records calls.
    """

    @abstractmethod
    def validate(self, url: str) -> ValidatedTarget: ...

    @abstractmethod
    async def send(
        self,
        *,
        method: str,
        validated: ValidatedTarget,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], bytes]: ...
