from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.exceptions import EndpointNotFoundError
from webhook_inspector.infrastructure.repositories.endpoint_repository import (
    PostgresEndpointRepository,
)


@pytest.mark.asyncio
async def test_update_persists_signature_config(pg_session):
    repo = PostgresEndpointRepository(pg_session)
    endpoint = Endpoint.create(token="test-token-update", ttl_days=7)
    await repo.save(endpoint)
    await pg_session.commit()

    # Update with signature config
    endpoint.signature_provider = "stripe"
    endpoint.signature_secret_encrypted = b"\x00" * 12 + b"ciphertext+tag"
    await repo.update(endpoint)
    await pg_session.commit()

    # Read back
    reloaded = await repo.find_by_token("test-token-update")
    assert reloaded is not None
    assert reloaded.signature_provider == "stripe"
    assert reloaded.signature_secret_encrypted == b"\x00" * 12 + b"ciphertext+tag"


@pytest.mark.asyncio
async def test_update_unknown_id_raises(pg_session):
    repo = PostgresEndpointRepository(pg_session)
    now = datetime.now(UTC)
    endpoint = Endpoint(
        id=uuid4(),
        token="ghost",
        created_at=now,
        expires_at=now + timedelta(days=7),
        request_count=0,
    )
    with pytest.raises(EndpointNotFoundError):
        await repo.update(endpoint)
