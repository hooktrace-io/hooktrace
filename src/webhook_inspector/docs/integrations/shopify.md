# Shopify webhooks

## What hooktrace validates

When you configure `shopify` as your signature provider in the viewer's
Settings panel, hooktrace verifies each captured request's
`X-Shopify-Hmac-Sha256` header against your Shopify app's API secret.

## Signature header

- Header name: `X-Shopify-Hmac-Sha256`
- Algorithm: HMAC-SHA256
- Secret format: the API secret key of the Shopify app that owns the webhook
  (a hex-like string).
- Signed payload: the raw request body, byte for byte.
- Header format: a **base64-encoded** digest — NOT hex (Shopify is the
  outlier here vs. GitHub/Stripe/Slack).

## Where to find the secret

Shopify Partners dashboard → **Apps → (your app) → API credentials → API
secret key**.

## Sample payload (truncated)

```json
{
  "id": 820982911946154508,
  "admin_graphql_api_id": "gid://shopify/Order/820982911946154508",
  "name": "#1001",
  "financial_status": "paid",
  "total_price": "29.99",
  "currency": "USD"
}
```

The matching topic is typically `orders/create`, sent as the
`X-Shopify-Topic` header alongside the body.

## Reference

- Official docs: <https://shopify.dev/docs/apps/build/webhooks/subscribe/https#verify-the-webhook>
