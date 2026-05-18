from uuid import UUID

from webhook_inspector.domain.entities.forward import Forward
from webhook_inspector.domain.ports.forward_repository import ForwardRepository


class FakeForwardRepository(ForwardRepository):
    def __init__(self) -> None:
        self.saved: list[Forward] = []
        self.updated: list[Forward] = []

    async def save(self, forward: Forward) -> None:
        self.saved.append(forward)

    async def find_by_id(self, forward_id: UUID) -> Forward | None:
        # Return the most-recent state (search updates first, then saves)
        for f in reversed(self.updated):
            if f.id == forward_id:
                return f
        for f in self.saved:
            if f.id == forward_id:
                return f
        return None

    async def update(self, forward: Forward) -> None:
        self.updated.append(forward)

    async def list_by_request(self, request_id: UUID) -> list[Forward]:
        return [f for f in self.saved if f.request_id == request_id]
