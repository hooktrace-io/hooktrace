# Integration guides

hooktrace ships with built-in HMAC signature validation for nine providers and
signs its own outbound forwards so your receivers can verify them.

## Verifying hooktrace forwards

- **[Verifying forwards](verifying-forwards)** — the public contract for the
  `X-Hooktrace-Signature` header on forwarded POSTs (Stripe-compatible
  HMAC-SHA256), plus idempotency keys and the retry schedule.

## Inbound signature schemes hooktrace validates

These pages describe the scheme each provider follows when sending webhooks
to you, so you know what hooktrace checks when you paste a signing secret
into the viewer's Settings panel.

- **[Stripe](stripe)** — `Stripe-Signature: t=…,v1=…`, HMAC-SHA256, hex.
- **[GitHub](github)** — `X-Hub-Signature-256: sha256=…`, HMAC-SHA256, hex.
- **[Shopify](shopify)** — `X-Shopify-Hmac-Sha256: …`, HMAC-SHA256, base64.
- **[Twilio](twilio)** — `X-Twilio-Signature: …`, HMAC-SHA1, base64 (URL +
  sorted form params; hooktrace runs the simplified body-only variant for
  now).
- **[Mailgun](mailgun)** — signature is **in the body**, not a header;
  HMAC-SHA256 over `timestamp+token`.
- **[Discord](discord)** — `X-Signature-Ed25519` + `X-Signature-Timestamp`,
  **Ed25519** (asymmetric — the secret field holds a public key).
- **[Slack](slack)** — `X-Slack-Signature: v0=…` + `X-Slack-Request-Timestamp`,
  HMAC-SHA256, hex.
- **[Zapier](zapier)** — `X-Hook-Signature: …`, HMAC-SHA256, hex. Zapier
  does not sign by default; a signing step inside the Zap is required.
- **[n8n](n8n)** — `X-N8N-Signature: …`, HMAC-SHA256, hex.
