"""Manually abandon a forward from the DLQ UI.

Soft-delete: transitions the row to 'abandoned' from any non-terminal
state, recording the timestamp and a fixed `final_error` for audit. Used
when the owner has accepted that a delivery will never succeed (e.g. the
target endpoint has been retired).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from webhook_inspector.domain.entities.forward import Forward
from webhook_inspector.domain.exceptions import EndpointNotFoundError
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository
from webhook_inspector.domain.ports.forward_repository import ForwardRepository

__all__ = ["AbandonForward", "ForwardNotFoundError"]


class ForwardNotFoundError(Exception):
    """Forward not found under the given endpoint (or already terminal)."""


@dataclass
class AbandonForward:
    endpoint_repo: EndpointRepository
    forward_repo: ForwardRepository

    async def execute(self, *, token: str, forward_id: UUID) -> Forward:
        endpoint = await self.endpoint_repo.find_by_token(token)
        if endpoint is None:
            raise EndpointNotFoundError(token)

        now = datetime.now(UTC)
        abandoned = await self.forward_repo.abandon(forward_id, endpoint.id, now=now)
        if abandoned is None:
            raise ForwardNotFoundError(f"forward {forward_id} not found under endpoint {token}")
        return abandoned
