"""Canonical in-memory BlobStorage for unit tests."""

from webhook_inspector.domain.ports.blob_storage import BlobStorage


class FakeBlobStorage(BlobStorage):
    """In-memory BlobStorage.

    Pass ``fail=True`` to simulate a storage outage: every ``put`` call raises
    ``RuntimeError("storage down")``.  ``get`` always works even in fail mode
    (so tests can check what would have been stored separately).
    """

    def __init__(self, blobs: dict[str, bytes] | None = None, *, fail: bool = False):
        self.puts: dict[str, bytes] = dict(blobs) if blobs else {}
        self.fail = fail

    async def put(self, key: str, data: bytes) -> None:
        if self.fail:
            raise RuntimeError("storage down")
        self.puts[key] = data

    async def get(self, key: str) -> bytes | None:
        return self.puts.get(key)
