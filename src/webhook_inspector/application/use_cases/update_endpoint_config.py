from dataclasses import dataclass

from webhook_inspector.domain.exceptions import EndpointNotFoundError
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository
from webhook_inspector.infrastructure.crypto.secrets import encrypt_secret


@dataclass
class UpdateEndpointConfig:
    endpoint_repo: EndpointRepository
    secrets_key: bytes  # 32 bytes decoded from Settings.secrets_encryption_key

    async def execute(
        self,
        *,
        token: str,
        signature_provider: str | None = None,
        signature_secret: str | None = None,
        forward_url: str | None = None,
        forward_headers: dict[str, str] | None = None,
        forward_secret: str | None = None,
    ) -> None:
        endpoint = await self.endpoint_repo.find_by_token(token)
        if endpoint is None:
            raise EndpointNotFoundError(f"endpoint not found: {token}")

        if signature_provider is not None:
            # Provider validity is enforced by the Pydantic Literal at the boundary
            # (returns 422 automatically). No need to re-validate here.
            endpoint.signature_provider = signature_provider
            if signature_secret is not None:
                endpoint.signature_secret_encrypted = encrypt_secret(
                    self.secrets_key, signature_secret
                )

        if forward_url is not None:
            endpoint.forward_url = forward_url
            endpoint.forward_headers = forward_headers
            if forward_secret is not None:
                endpoint.forward_secret_encrypted = encrypt_secret(self.secrets_key, forward_secret)

        await self.endpoint_repo.update(endpoint)
