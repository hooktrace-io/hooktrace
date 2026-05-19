from dataclasses import dataclass

from webhook_inspector.application.services.slug_validator import validate_slug
from webhook_inspector.domain.entities.endpoint import (
    DEFAULT_RESPONSE_BODY,
    DEFAULT_RESPONSE_DELAY_MS,
    DEFAULT_RESPONSE_STATUS_CODE,
    Endpoint,
)
from webhook_inspector.domain.exceptions import SlugDenylistedError
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository
from webhook_inspector.domain.ports.metrics_collector import MetricsCollector
from webhook_inspector.domain.services.slug_denylist import is_denylisted
from webhook_inspector.domain.services.token_generator import generate_token


@dataclass
class CreateEndpoint:
    repo: EndpointRepository
    ttl_days: int
    metrics: MetricsCollector

    async def execute(
        self,
        *,
        slug: str | None = None,
        response_status_code: int = DEFAULT_RESPONSE_STATUS_CODE,
        response_body: str = DEFAULT_RESPONSE_BODY,
        response_headers: dict[str, str] | None = None,
        response_delay_ms: int = DEFAULT_RESPONSE_DELAY_MS,
    ) -> Endpoint:
        if slug is not None:
            validate_slug(slug)
            if is_denylisted(slug):
                raise SlugDenylistedError(f"slug rejected: {slug}")
            token = slug
        else:
            token = generate_token()

        endpoint = Endpoint.create(
            token=token,
            ttl_days=self.ttl_days,
            response_status_code=response_status_code,
            response_body=response_body,
            response_headers=response_headers,
            response_delay_ms=response_delay_ms,
        )
        await self.repo.save(endpoint)
        self.metrics.endpoint_created()
        return endpoint
