import pytest

from webhook_inspector.domain.services.slug_denylist import is_denylisted


@pytest.mark.parametrize(
    "slug",
    [
        "stripe",
        "STRIPE",
        "stripe-test",
        "my-stripe-clone",
        "paypal-fake",
        "verify-your-account",
        "signin-google",
        "apple-id-update",
        "root",
        "admin-panel",
    ],
)
def test_denylisted(slug):
    assert is_denylisted(slug)


@pytest.mark.parametrize(
    "slug",
    [
        "my-app-webhooks",
        "user-events",
        "test123",
        "abc-def-ghi",
        "webhooks-prod",
        "company-events-2026",
    ],
)
def test_allowed(slug):
    assert not is_denylisted(slug)
