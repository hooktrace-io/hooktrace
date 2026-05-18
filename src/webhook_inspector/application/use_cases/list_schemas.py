from dataclasses import dataclass

from webhook_inspector.domain.entities.inferred_schema import InferredSchema
from webhook_inspector.domain.exceptions import EndpointNotFoundError
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository
from webhook_inspector.domain.ports.schema_repository import SchemaRepository


@dataclass
class ListSchemas:
    endpoint_repo: EndpointRepository
    schema_repo: SchemaRepository

    async def execute_for_token(self, token: str) -> list[InferredSchema]:
        endpoint = await self.endpoint_repo.find_by_token(token)
        if endpoint is None:
            raise EndpointNotFoundError(token)
        return await self.schema_repo.list_by_endpoint(endpoint.id)
