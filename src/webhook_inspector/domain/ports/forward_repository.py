from abc import ABC, abstractmethod
from uuid import UUID

from webhook_inspector.domain.entities.forward import Forward


class ForwardRepository(ABC):
    @abstractmethod
    async def save(self, forward: Forward) -> None: ...

    @abstractmethod
    async def find_by_id(self, forward_id: UUID) -> Forward | None: ...

    @abstractmethod
    async def update(self, forward: Forward) -> None: ...

    @abstractmethod
    async def list_by_request(self, request_id: UUID) -> list[Forward]: ...
