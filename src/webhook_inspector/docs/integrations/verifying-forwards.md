# Verifying hooktrace forwards

When you configure a forward target with a secret in hooktrace, every outbound
POST is signed so your backend can verify it came from hooktrace and was not
tampered with.

## Signature header

```
X-Hooktrace-Signature: t=<unix_seconds>,v1=<hex_sha256>
```

- `t` — the Unix timestamp (seconds) when hooktrace signed the payload.
- `v1` — `HMAC-SHA256(secret, f"{t}.{body}").hexdigest()`.

This is the same scheme Stripe uses for `Stripe-Signature`. If you already
verify Stripe webhooks, the code is identical — only the header name and the
shared secret change.

## Verifying — Python example

```python
import hashlib
import hmac
import time


def verify_hooktrace(
    header: str, body: bytes, secret: bytes, tolerance: int = 300
) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    timestamp = parts.get("t")
    received = parts.get("v1")
    if not timestamp or not received:
        return False

    # Replay protection: reject signatures older than `tolerance` seconds.
    if abs(int(time.time()) - int(timestamp)) > tolerance:
        return False

    expected = hmac.new(
        secret, f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, received)
```

## Verifying — Node.js example

```javascript
import crypto from "node:crypto";

function verifyHooktrace(header, body, secret, tolerance = 300) {
  const parts = Object.fromEntries(
    header.split(",").map(p => p.split("=", 2)).filter(([k]) => k)
  );
  const timestamp = parts.t;
  const received = parts.v1;
  if (!timestamp || !received) return false;

  if (Math.abs(Math.floor(Date.now() / 1000) - parseInt(timestamp, 10)) > tolerance) {
    return false;
  }

  const expected = crypto.createHmac("sha256", secret)
    .update(`${timestamp}.`)
    .update(body)
    .digest("hex");

  // Constant-time compare
  const a = Buffer.from(expected, "hex");
  const b = Buffer.from(received, "hex");
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}
```

## Idempotency

Every outbound request also carries:

```
Idempotency-Key: <forward_id>:<attempt_count>
X-Hooktrace-Forward-Id: <forward_id>
```

The `forward_id` is stable across retries; `attempt_count` increments each
retry attempt (1..5). If your receiver sees the same `Idempotency-Key` twice,
it is a retry — dedupe on the receiver side.

## Retry policy

hooktrace retries failed forwards up to 5 total attempts with exponential
backoff. The delay shown is the wait between the previous attempt and the
next one.

| Attempt | Delay since last |
|---------|------------------|
| 1       | (immediate)      |
| 2       | 30 seconds       |
| 3       | 2 minutes        |
| 4       | 10 minutes       |
| 5       | 1 hour           |

After the 5th attempt fails, the forward enters the DLQ (status: `dead`). You
can manually retry from the viewer's Forwards tab.

Retryable HTTP status codes: `408`, `425`, `429`, `500`, `502`, `503`, `504`,
`507`, `508`. Any other 4xx is treated as a hard failure and goes straight to
`dead`.

## Failure modes you should plan for

- **Two deliveries with the same `Idempotency-Key` and `attempt_count`** —
  possible during arq worker restarts. Dedupe on the full key.
- **A retry after a long delay** — your receiver may have already processed
  the original. Be idempotent.
- **No retry at all** — if hooktrace cannot enqueue (e.g. Redis outage), the
  forward stays in `pending` and never fires. The hooktrace operator runs the
  **Redrive** action manually to recover after Redis is back.
