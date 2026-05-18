from typing import Literal

from pydantic import BaseModel, Field

# Tied to the registry in get_validator(). Adding a 10th provider = update both.
SignatureProvider = Literal[
    "stripe",
    "github",
    "shopify",
    "twilio",
    "mailgun",
    "discord",
    "slack",
    "zapier",
    "n8n",
]


class SignatureConfig(BaseModel):
    provider: SignatureProvider = Field(..., description="One of the 9 supported providers.")
    secret: str = Field(..., min_length=1)


class EndpointConfigPatch(BaseModel):
    """Partial update of endpoint config. Each field is independently optional.
    Future extensions: `forward: ForwardConfig | None` for forwarding configuration.
    `transform: str | None` is deferred to V4 — when it lands, it slots in as a new optional field.
    """

    signature: SignatureConfig | None = None
