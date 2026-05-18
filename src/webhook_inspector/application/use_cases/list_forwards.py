"""List forwards under an endpoint with optional status filter + cursor pagination.

Read-only use case for the DLQ UI. Delegates filtering and pagination to
the repository — the use case only authorizes via token and forwards the
query parameters as-is.
"""

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from webhook_inspector.domain.entities.forward import Forward, ForwardStatus
from webhook_inspector.domain.exceptions import EndpointNotFoundError
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository
from webhook_inspector.domain.ports.forward_repository import ForwardRepository

__all__ = ["ListForwards"]


@dataclass
class ListForwards:
    endpoint_repo: EndpointRepository
    forward_repo: ForwardRepository

    async def execute(
        self,
        *,
        token: str,
        statuses: list[str] | None,
        limit: int,
        before_id: UUID | None,
    ) -> list[Forward]:
        endpoint = await self.endpoint_repo.find_by_token(token)
        if endpoint is None:
            raise EndpointNotFoundError(token)
        # Route layer validates membership in ForwardStatus via Pydantic Literal;
        # cast at the boundary to satisfy the repo's narrower type.
        narrowed = cast(list[ForwardStatus] | None, statuses)
        return await self.forward_repo.list_by_endpoint(
            endpoint.id,
            statuses=narrowed,
            limit=limit,
            before_id=before_id,
        )
