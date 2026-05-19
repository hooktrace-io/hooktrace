# Slack webhooks

## What hooktrace validates

When you configure `slack` as your signature provider in the viewer's
Settings panel, hooktrace verifies each captured request's
`X-Slack-Signature` header against your Slack app's signing secret.

## Signature header

- Header names:
  - `X-Slack-Signature` — the digest, prefixed with `v0=`.
  - `X-Slack-Request-Timestamp` — Unix seconds.
- Algorithm: HMAC-SHA256
- Secret format: the **Signing Secret** of the Slack app (a hex-like
  string, distinct from the Bot User OAuth Token).
- Signed payload: `f"v0:{timestamp}:{body}".encode()` — the literal `v0:`
  prefix, the timestamp, a colon, then the raw body.
- Header format: `v0=<hex_sha256>`.

## Where to find the secret

Slack API dashboard → **Your Apps → (your app) → Basic Information → App
Credentials → Signing Secret**.

## Sample payload (truncated, URL-encoded form for Events API)

```
token=verification-token
team_id=T12345678
event_type=event_callback
event=%7B%22type%22%3A%22message%22%2C%22text%22%3A%22hello%22%7D
event_id=Ev08K1A2BC
```

JSON payloads from the Events API and modal interactions follow the same
signing scheme — only the body shape differs.

## Reference

- Official docs: <https://api.slack.com/authentication/verifying-requests-from-slack>
