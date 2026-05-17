from webhook_inspector.domain.services.hmac.base import HmacValidator
from webhook_inspector.domain.services.hmac.discord import DiscordValidator
from webhook_inspector.domain.services.hmac.github import GithubValidator
from webhook_inspector.domain.services.hmac.mailgun import MailgunValidator
from webhook_inspector.domain.services.hmac.n8n import N8nValidator
from webhook_inspector.domain.services.hmac.shopify import ShopifyValidator
from webhook_inspector.domain.services.hmac.slack import SlackValidator
from webhook_inspector.domain.services.hmac.stripe import StripeValidator
from webhook_inspector.domain.services.hmac.twilio import TwilioValidator
from webhook_inspector.domain.services.hmac.zapier import ZapierValidator

_VALIDATORS: dict[str, type[HmacValidator]] = {
    "stripe": StripeValidator,
    "github": GithubValidator,
    "shopify": ShopifyValidator,
    "twilio": TwilioValidator,
    "mailgun": MailgunValidator,
    "discord": DiscordValidator,
    "slack": SlackValidator,
    "zapier": ZapierValidator,
    "n8n": N8nValidator,
}


def get_validator(provider: str) -> HmacValidator | None:
    cls = _VALIDATORS.get(provider.lower())
    return cls() if cls else None
