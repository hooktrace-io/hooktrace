from uuid import UUID

from webhook_inspector.domain.ports.forward_queue import ForwardQueue


class FakeForwardQueue(ForwardQueue):
    """In-memory ForwardQueue for unit tests. Records all enqueue calls."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[UUID, int]] = []

    async def enqueue(self, forward_id: UUID, *, defer_seconds: int = 0) -> None:
        self.enqueued.append((forward_id, defer_seconds))
