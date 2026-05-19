"""Slug denylist: substring match against brand names + offensive words.

Updates: edit this list + git commit + deploy. NOT loaded from DB at request
time — that would be one Redis/DB round-trip per CreateEndpoint, for a list
that changes monthly at most. Code-versioned + bumped via PR.

Matching is substring(case-insensitive), not exact, so `stripe-test`,
`paypal-fake`, `apple-id-verify` are all blocked. False positives (e.g.
`stripe` legitimately in a non-payment context) are accepted as cost ;
the affected user can choose a different vanity slug.
"""

DENYLISTED_SUBSTRINGS: frozenset[str] = frozenset(
    {
        # Payment / financial
        "stripe",
        "paypal",
        "square",
        "klarna",
        # Big tech
        "google",
        "amazon",
        "microsoft",
        "apple",
        "facebook",
        "meta",
        "twitter",
        "tiktok",
        "openai",
        "anthropic",
        "github",
        "discord",
        "slack",
        # Webhook senders we list as supported (avoid confusion with official)
        "shopify",
        "twilio",
        "mailgun",
        "zapier",
        "n8n",
        # Admin / system reserved
        "admin",
        "root",
        "system",
        "support",
        # Common phishing patterns
        "verify",
        "login",
        "signin",
        "secure",
        "account-update",
        "password-reset",
    }
)


def is_denylisted(slug: str) -> bool:
    lower = slug.lower()
    return any(needle in lower for needle in DENYLISTED_SUBSTRINGS)
