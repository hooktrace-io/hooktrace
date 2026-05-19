from pydantic import BaseModel, Field, HttpUrl

from webhook_inspector.domain.services.integration_detector import IntegrationName

# Re-exported under its old name so callers that imported `SignatureProvider`
# don't have to change. The set of supported providers is the same set as the
# 9 detected integrations (1:1 with hmac.factory._VALIDATORS), so the type
# alias from the domain layer is the single source of truth.
SignatureProvider = IntegrationName


class SignatureConfig(BaseModel):
    provider: SignatureProvider = Field(..., description="One of the 9 supported providers.")
    secret: str = Field(..., min_length=1)


class ForwardConfig(BaseModel):
    url: HttpUrl
    headers: dict[str, str] | None = None
    secret: str | None = Field(default=None, min_length=8, max_length=128)


class EndpointConfigPatch(BaseModel):
    """Partial update of endpoint config. Each field is independently optional.
    `transform: str | None` is deferred to V4 — when it lands, it slots in as a new optional field.
    """

    signature: SignatureConfig | None = None
    forward: ForwardConfig | None = None
