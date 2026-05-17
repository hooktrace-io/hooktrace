from webhook_inspector.domain.services.hmac.factory import get_validator
from webhook_inspector.domain.services.hmac.stripe import StripeValidator


def test_factory_returns_stripe():
    v = get_validator("stripe")
    assert isinstance(v, StripeValidator)


def test_factory_case_insensitive():
    assert get_validator("STRIPE") is not None
    assert get_validator("Stripe") is not None


def test_factory_unknown_returns_none():
    assert get_validator("not-a-real-provider") is None


def test_factory_all_9_providers_resolvable():
    for name in [
        "stripe",
        "github",
        "shopify",
        "twilio",
        "mailgun",
        "discord",
        "slack",
        "zapier",
        "n8n",
    ]:
        assert get_validator(name) is not None, f"factory missed {name}"
