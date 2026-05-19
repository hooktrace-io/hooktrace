from uuid import UUID

from arq import ArqRedis

from webhook_inspector.domain.ports.forward_queue import ForwardQueue


class ArqForwardQueue(ForwardQueue):
    """arq-backed ForwardQueue. Reuses the worker's RedisPool, so connection
    handling is centralized.

    The `_job_id` includes `defer_seconds` so different scheduling rounds
    (attempt 1 → 30s, attempt 2 → 120s, …) don't deduplicate each other.
    Combined with claim_for_attempt's atomic DB check, duplicate firing
    is bounded to benign no-ops.
    """

    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def enqueue(self, forward_id: UUID, *, defer_seconds: int = 0) -> None:
        await self._pool.enqueue_job(
            "execute_forward",
            str(forward_id),
            _job_id=f"forward:{forward_id}:{defer_seconds}",
            _defer_by=defer_seconds,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()
