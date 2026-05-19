# Zapier webhooks

## What hooktrace validates

When you configure `zapier` as your signature provider in the viewer's
Settings panel, hooktrace verifies each captured request's `X-Hook-Signature`
header against the secret configured by the signing step inside your Zap.

## Signature header

- Header name: `X-Hook-Signature`
- Algorithm: HMAC-SHA256
- Secret format: the secret you configured in the Zap's signing step
  (Zapier's Code step, a Formatter step, or middleware).
- Signed payload: the raw request body, byte for byte.
- Header format: a hex-encoded digest.

## Zapier does not sign by default

Out of the box, Zapier's webhook trigger sends **no signature header** — only
a `User-Agent: Zapier` identifies the source. If your Zap is not configured
to sign outbound webhooks, hooktrace will show `MISSING` for the signature.
Leave `signature_provider` unset in that case, or add a signing step inside
the Zap.

A minimal signing step (Code by Zapier, Python):

```python
import hashlib
import hmac

secret = b"your-shared-secret"
body = input_data["body"].encode()
signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
output = {"signature": signature}
```

Then attach `output.signature` as the `X-Hook-Signature` header on the
outbound webhook step.

## Where to find the secret

Wherever you stored it when wiring up the signing step. There is no
canonical Zapier UI for "signing key" — it is whatever string you generated.

## Sample payload (truncated)

```json
{
  "trigger_event": "new_record",
  "record_id": "rec_abc123",
  "table": "Customers",
  "fields": {
    "email": "alice@example.com",
    "plan": "pro"
  }
}
```

## Reference

- Official docs: <https://zapier.com/help/create/code-webhooks/send-webhooks-in-zaps>
