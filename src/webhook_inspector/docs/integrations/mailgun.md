# Mailgun webhooks

## What hooktrace validates

When you configure `mailgun` as your signature provider in the viewer's
Settings panel, hooktrace verifies the signature carried in the **request
body** (Mailgun does not put it in a header) against your Mailgun HTTP
webhook signing key.

## Signature location

Mailgun is the odd one out: the signature is **not** a header. The
form-encoded body contains three fields hooktrace reads:

- `timestamp` — Unix seconds.
- `token` — a short opaque per-event token.
- `signature` — the hex digest to verify.

## Signature algorithm

- Algorithm: HMAC-SHA256
- Secret format: the Mailgun HTTP webhook signing key.
- Signed payload: `f"{timestamp}{token}".encode()` — the two values
  concatenated with no separator.
- Encoding: hex digest, lowercase.

If the body is larger than 64 KiB, hooktrace returns `INVALID` without
computing the HMAC (Mailgun events are well under this in practice).

## Where to find the secret

Mailgun Control Panel → **Sending → Webhooks → HTTP webhook signing key**
(per-domain).

## Sample payload (truncated, form-encoded)

```
event-data=%7B%22event%22%3A%22delivered%22%2C%22recipient%22%3A%22alice%40example.com%22%7D
signature=e9c12f...
token=8f3b...
timestamp=1717070400
```

## Reference

- Official docs: <https://documentation.mailgun.com/docs/mailgun/user-manual/get-started/#webhooks>
