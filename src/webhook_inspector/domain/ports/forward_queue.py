from abc import ABC, abstractmethod
from uuid import UUID


class ForwardQueue(ABC):
    """Transport-agnostic queue for forward jobs.

    Implementations:
    - ArqForwardQueue (prod): pushes onto Redis via arq's RedisPool
    - FakeForwardQueue (tests): records enqueue calls in memory
    """

    @abstractmethod
    async def enqueue(self, forward_id: UUID, *, defer_seconds: int = 0) -> None:
        """Schedule `execute_forward(forward_id)` to run after `defer_seconds`.
        defer_seconds=0 → run as soon as a worker is free.
        Implementations MUST be idempotent w.r.t. duplicate enqueues of the
        same forward_id — execute_forward's atomic state claim handles the
        in-DB side of idempotence; ArqForwardQueue uses arq's `_job_id` for
        the in-Redis side.
        """

    @abstractmethod
    async def aclose(self) -> None:
        """Release any underlying transport resource (e.g. a Redis pool).

        Stateless impls (Null/Fake) make this a no-op. Lifespans call it
        on shutdown so callers don't need to reach into impl-specific
        private attributes.
        """
