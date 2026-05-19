# Discord webhooks

## What hooktrace validates

When you configure `discord` as your signature provider in the viewer's
Settings panel, hooktrace verifies each captured interaction's Ed25519
signature against your Discord application's public key.

## Signature header

- Header names:
  - `X-Signature-Ed25519` — the hex-encoded signature (128 hex chars).
  - `X-Signature-Timestamp` — Unix seconds (string).
- Algorithm: **Ed25519** (asymmetric) — NOT HMAC.
- "Secret" format: Discord's **application public key**, hex-encoded
  (32 bytes = 64 hex chars).
- Signed payload: `(timestamp + body).encode("utf-8")` — the timestamp string
  prepended to the raw body.

Because Discord uses asymmetric signing, the value you paste into hooktrace
is a **public key**, not a shared secret. It is safe to share and only proves
authenticity; the private key never leaves Discord's servers. The viewer
still labels the field "secret" for UI uniformity across integrations.

## Where to find the public key

Discord Developer Portal → **Applications → (your app) → General Information
→ Public Key**.

## Sample payload (truncated)

```json
{
  "type": 2,
  "id": "112233445566778899",
  "application_id": "1234567890",
  "data": {
    "name": "ping",
    "type": 1
  },
  "token": "aW50ZXJhY3Rpb24tdG9rZW4"
}
```

`type: 1` is a `PING` validation handshake; `type: 2` is an
`APPLICATION_COMMAND`.

## Reference

- Official docs: <https://discord.com/developers/docs/interactions/overview#preparing-for-interactions>
