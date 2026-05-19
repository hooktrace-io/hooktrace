"""Fetch a single Endpoint by token. Thin wrapper around EndpointRepository.

Exists so the viewer route (and any future read-only consumer that needs the
full Endpoint entity, e.g. for expires_at on the countdown badge) depends on
an application-layer use case rather than reaching into the repo directly —
preserves the Clean Architecture layering.
"""

from dataclasses import dataclass

from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.exceptions import EndpointNotFoundError
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository

__all__ = ["EndpointNotFoundError", "GetEndpoint"]


@dataclass
class GetEndpoint:
    endpoint_repo: EndpointRepository

    async def execute(self, *, token: str) -> Endpoint:
        endpoint = await self.endpoint_repo.find_by_token(token)
        if endpoint is None:
            raise EndpointNotFoundError(token)
        return endpoint
