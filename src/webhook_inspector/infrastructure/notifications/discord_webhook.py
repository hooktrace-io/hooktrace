"""Minimal Discord webhook sender. The URL is created once in Discord
(channel settings → Integrations → Webhooks), stored as Fly secret
ABUSE_WEBHOOK_URL on the worker app.
"""

import httpx


async def post_discord_alert(webhook_url: str, message: str) -> None:
    """Best-effort POST. Failure is logged at caller-level; we don't raise."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(webhook_url, json={"content": message[:2000]})
