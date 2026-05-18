from uuid import UUID

from webhook_inspector.domain.entities.replay import Replay
from webhook_inspector.domain.ports.replay_repository import ReplayRepository


class FakeReplayRepository(ReplayRepository):
    def __init__(self) -> None:
        self.saved: list[Replay] = []

    async def save(self, replay: Replay) -> None:
        self.saved.append(replay)

    async def list_by_request(self, request_id: UUID) -> list[Replay]:
        return [r for r in self.saved if r.request_id == request_id]
