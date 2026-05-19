"""Unit tests for the Discord webhook sender.

We use respx to assert the HTTP request body, and a direct monkeypatch on
httpx.AsyncClient to assert the constructor's timeout arg (respx mocks at
the transport layer, so it doesn't expose constructor kwargs).
"""

import httpx
import respx

from webhook_inspector.infrastructure.notifications import discord_webhook
from webhook_inspector.infrastructure.notifications.discord_webhook import (
    post_discord_alert,
)

WEBHOOK_URL = "https://discord.com/api/webhooks/123/abc"


async def test_posts_message_to_webhook_url():
    import json

    with respx.mock() as respx_mock:
        route = respx_mock.post(WEBHOOK_URL).respond(status_code=204)

        await post_discord_alert(WEBHOOK_URL, "hello")

    assert route.called
    payload = json.loads(route.calls.last.request.read())
    assert payload == {"content": "hello"}


async def test_truncates_message_to_2000_chars():
    with respx.mock() as respx_mock:
        route = respx_mock.post(WEBHOOK_URL).respond(status_code=204)

        await post_discord_alert(WEBHOOK_URL, "x" * 3000)

    assert route.called
    import json

    payload = json.loads(route.calls.last.request.read())
    assert len(payload["content"]) == 2000
    assert payload["content"] == "x" * 2000


async def test_uses_timeout(monkeypatch):
    """Verify timeout=10.0 is passed to AsyncClient(...) constructor."""
    captured_kwargs: dict[str, object] = {}
    real_async_client = httpx.AsyncClient

    class CapturingClient(real_async_client):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            captured_kwargs.update(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(discord_webhook.httpx, "AsyncClient", CapturingClient)

    with respx.mock() as respx_mock:
        respx_mock.post(WEBHOOK_URL).respond(status_code=204)
        await post_discord_alert(WEBHOOK_URL, "hi")

    assert captured_kwargs.get("timeout") == 10.0
