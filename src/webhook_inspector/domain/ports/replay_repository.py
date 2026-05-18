from abc import ABC, abstractmethod
from uuid import UUID

from webhook_inspector.domain.entities.replay import Replay


class ReplayRepository(ABC):
    @abstractmethod
    async def save(self, replay: Replay) -> None: ...

    @abstractmethod
    async def list_by_request(self, request_id: UUID) -> list[Replay]: ...
