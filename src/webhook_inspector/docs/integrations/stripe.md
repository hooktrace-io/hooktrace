# Stripe webhooks

## What hooktrace validates

When you configure `stripe` as your signature provider in the viewer's
Settings panel, hooktrace verifies each captured request's `Stripe-Signature`
header against your Stripe webhook signing secret.

## Signature header

- Header name: `Stripe-Signature`
- Algorithm: HMAC-SHA256
- Secret format: `whsec_…` (a hex-like string prefixed with `whsec_`)
- Signed payload: `f"{timestamp}.{body}"` — the `t` value from the header
  joined to the raw JSON body by a literal dot.
- Header format: `t=<unix_seconds>,v1=<hex_sha256>` (multiple `v1=` chunks may
  appear during key rotation; hooktrace accepts a match against any of them).

## Where to find the secret

Stripe Dashboard → **Developers → Webhooks → (your endpoint) → Signing
secret**.

## Sample payload (truncated)

```json
{
  "id": "evt_1NQz3J2eZvKYlo2C",
  "object": "event",
  "type": "invoice.paid",
  "data": {
    "object": {
      "id": "in_1NQz3I2eZvKYlo2C",
      "amount_paid": 2000,
      "currency": "usd"
    }
  }
}
```

## Reference

- Official docs: <https://docs.stripe.com/webhooks#verify-manually>
