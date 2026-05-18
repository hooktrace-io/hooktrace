"""Per-status forward counts for the DLQ dashboard header."""

from dataclasses import dataclass
from typing import cast

from webhook_inspector.domain.exceptions import EndpointNotFoundError
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository
from webhook_inspector.domain.ports.forward_repository import ForwardRepository

__all__ = ["GetForwardStats"]


@dataclass
class GetForwardStats:
    endpoint_repo: EndpointRepository
    forward_repo: ForwardRepository

    async def execute(self, *, token: str) -> dict[str, int]:
        endpoint = await self.endpoint_repo.find_by_token(token)
        if endpoint is None:
            raise EndpointNotFoundError(token)
        # repo returns dict[ForwardStatus, int]; widen to dict[str, int] for the
        # route layer (Pydantic re-validates the keys against the Literal there).
        return cast(dict[str, int], await self.forward_repo.count_by_status(endpoint.id))
