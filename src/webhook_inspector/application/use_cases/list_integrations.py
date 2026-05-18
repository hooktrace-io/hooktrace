"""Aggregate captured requests by detected integration AND by signature
validation status. The combined view distinguishes legitimate sender
traffic from forged / unsigned attempts.
"""

from dataclasses import dataclass

from webhook_inspector.domain.entities.integration_aggregate import IntegrationAggregate
from webhook_inspector.domain.exceptions import EndpointNotFoundError
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository
from webhook_inspector.domain.ports.request_repository import RequestRepository

__all__ = ["IntegrationAggregate", "ListIntegrations"]


@dataclass
class ListIntegrations:
    endpoint_repo: EndpointRepository
    request_repo: RequestRepository

    async def execute_for_token(self, token: str) -> list[IntegrationAggregate]:
        endpoint = await self.endpoint_repo.find_by_token(token)
        if endpoint is None:
            raise EndpointNotFoundError(token)
        return await self.request_repo.aggregate_by_integration(endpoint.id)
