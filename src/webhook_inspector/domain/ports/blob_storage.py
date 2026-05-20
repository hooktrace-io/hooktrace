from abc import ABC, abstractmethod


class BlobStorage(ABC):
    """Persist large request bodies offloaded out of Postgres."""

    @abstractmethod
    async def put(self, key: str, data: bytes) -> None:
        """Store `data` under `key`. Overwrites any existing object."""

    @abstractmethod
    async def get(self, key: str) -> bytes | None:
        """Return the bytes stored under `key`, or None if not found."""
