"""Unit tests for UpdateEndpointConfig use case.

Covers the three branches: endpoint not found (raises), no-op when no fields
provided, and provider+secret happy path (encrypts secret, calls update()).
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.fakes import FakeEndpointRepo
from webhook_inspector.application.use_cases.update_endpoint_config import (
    UpdateEndpointConfig,
)
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.exceptions import EndpointNotFoundError
from webhook_inspector.infrastructure.crypto.secrets import decrypt_secret

_TEST_KEY = b"\x00" * 32


def _endpoint() -> Endpoint:
    return Endpoint(
        id=uuid4(),
        token="abc",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        request_count=0,
    )


async def test_raises_when_endpoint_not_found():
    use_case = UpdateEndpointConfig(endpoint_repo=FakeEndpointRepo(), secrets_key=_TEST_KEY)
    with pytest.raises(EndpointNotFoundError):
        await use_case.execute(token="missing", signature_provider="stripe", signature_secret="s")


async def test_no_op_when_no_fields_provided():
    repo = FakeEndpointRepo(seed=_endpoint())
    use_case = UpdateEndpointConfig(endpoint_repo=repo, secrets_key=_TEST_KEY)

    await use_case.execute(token="abc")

    assert len(repo.updated) == 1
    assert repo.updated[0].signature_provider is None
    assert repo.updated[0].signature_secret_encrypted is None


async def test_provider_and_secret_sets_both_fields_and_encrypts():
    repo = FakeEndpointRepo(seed=_endpoint())
    use_case = UpdateEndpointConfig(endpoint_repo=repo, secrets_key=_TEST_KEY)

    await use_case.execute(
        token="abc",
        signature_provider="stripe",
        signature_secret="whsec_test123",
    )

    assert len(repo.updated) == 1
    saved = repo.updated[0]
    assert saved.signature_provider == "stripe"
    assert saved.signature_secret_encrypted is not None
    # Round-trip the encrypted secret with the same key.
    assert decrypt_secret(_TEST_KEY, saved.signature_secret_encrypted) == "whsec_test123"


async def test_provider_without_secret_skips_encryption():
    repo = FakeEndpointRepo(seed=_endpoint())
    use_case = UpdateEndpointConfig(endpoint_repo=repo, secrets_key=_TEST_KEY)

    await use_case.execute(token="abc", signature_provider="stripe")

    assert len(repo.updated) == 1
    saved = repo.updated[0]
    assert saved.signature_provider == "stripe"
    assert saved.signature_secret_encrypted is None
