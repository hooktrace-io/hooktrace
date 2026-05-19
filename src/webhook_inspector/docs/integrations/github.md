# GitHub webhooks

## What hooktrace validates

When you configure `github` as your signature provider in the viewer's
Settings panel, hooktrace verifies each captured request's
`X-Hub-Signature-256` header against your GitHub webhook secret.

## Signature header

- Header name: `X-Hub-Signature-256`
- Algorithm: HMAC-SHA256
- Secret format: an opaque string you choose when creating the webhook.
- Signed payload: the raw request body, byte for byte.
- Header format: `sha256=<hex_sha256>` — hex digest prefixed with `sha256=`.

GitHub also sends an older `X-Hub-Signature` (SHA-1) header for backward
compatibility. hooktrace ignores it and validates the SHA-256 variant only.

## Where to find the secret

You set this when you create the webhook: repository or organization
**Settings → Webhooks → (your webhook) → Secret**. GitHub does not show it
again afterwards — store it somewhere safe.

## Sample payload (truncated)

```json
{
  "action": "opened",
  "number": 42,
  "pull_request": {
    "id": 1234567890,
    "title": "Fix flaky test",
    "user": { "login": "octocat" },
    "state": "open"
  }
}
```

## Reference

- Official docs: <https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries>
