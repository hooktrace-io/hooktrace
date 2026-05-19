# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in hooktrace, please report it privately. **Do not open a public GitHub issue.**

### Preferred channel

Open a [private security advisory](https://github.com/hooktrace-io/hooktrace/security/advisories/new) directly on GitHub. This keeps the report confidential and lets us collaborate on a fix before public disclosure.

### What to include

- A clear description of the vulnerability
- Steps to reproduce
- Affected version or commit SHA
- Impact assessment (data exposure, RCE, DoS, etc.)
- Suggested mitigation if you have one

### Response timeline

This is a side-project maintained by a single person. Best-effort response within 7 days. Critical issues (RCE, data leak) will be prioritized.

### Out of scope

- Reports against the public live instance at `app.hooktrace.io` involving denial of service (the instance runs on minimal compute by design — see "Rate limiting" below for the actual deployed limits)
- Best-practice recommendations without an exploitable scenario
- Reports about the AI-assisted development disclosure in `README.md`

### Supported versions

Only the latest commit on `main` is supported. There are no backported security fixes.

---

## V3 hardening — what's in place today

Documented so external testers know what to focus on (or skip) when probing the live instance.

### Inbound

- **Rate limit per-IP on the public ingestor** — sliding-window via Redis Lua. Currently 100 req/min/IP on `/h/{token}`. Excess returns `429` with `Retry-After`. Fails closed (503) if Redis is unavailable.
- **Rate limit per-token** — separate caps in the route layer, fail-open (owner-facing): 1000 captures/hour/token, 10 replays/hour/token.
- **Slug denylist** — blocks vanity URLs containing brand names (stripe, paypal, github, ...), admin reserved words (admin, root, ...), and common phishing patterns (verify, login, signin, ...). Substring match, case-insensitive. Code-versioned in `src/webhook_inspector/domain/services/slug_denylist.py`.
- **Body size cap** — ingestor returns `413` above `MAX_BODY_BYTES` (default 1 MiB).
- **SSRF guard on replay** — two-layer (parse-time host suffix block + DNS-resolved IP filter for private/loopback/link-local/cloud metadata). The same guard applies to the **forward** feature so outbound POSTs can't be redirected at hooktrace's own infrastructure.

### Secrets at rest

- **HMAC signature secrets** (for inbound validation) and **forward secrets** (for outbound signature) are encrypted with AES-256-GCM via the `SECRETS_ENCRYPTION_KEY` server-side key. They are never exposed via the API or rendered in templates.
- The key is a Fly secret. Loss of the key invalidates all stored secrets — no recovery path.

### Outbound forward signature contract

When the owner configures a forward target with a secret, every outbound POST hooktrace makes is signed:

```
X-Hooktrace-Signature: t=<unix_seconds>,v1=<hex_sha256>
```

Where `v1 = HMAC-SHA256(secret, f"{t}.{body}")`. Stripe-compatible scheme. The receiving backend should verify this signature + reject requests where `abs(now - t) > 300`. See [`docs/integrations/verifying-forwards`](https://app.hooktrace.io/docs/integrations/verifying-forwards) for verification snippets.

Each outbound POST also carries `Idempotency-Key: <forward_id>:<attempt_count>` for receiver-side dedup across retries.

### Anti-phishing detection (daily)

- `abuse_scan` arq cron runs daily at 03:30 UTC inside the worker app.
- Flags endpoints with ≥ 20 POST/PUT/PATCH captures AND zero successful forwards over the last 24h — the heuristic for "victim form harvesting via the viewer page".
- Flagged endpoints are NOT auto-frozen (false positive risk). The maintainer receives a Discord notification via `ABUSE_WEBHOOK_URL` for manual review.
- Retention for flagged endpoints is the same 30 days as unflagged.

### Network edge

- TLS terminated by Fly's HTTPS proxy. Let's Encrypt certificates managed by Fly.
- Cloudflare Registrar for the `hooktrace.io` domain. DNS-only mode (no proxy) — TLS lives at Fly.

### Authentication model

- **No accounts.** The bearer of an endpoint token has full authority over that endpoint. Tokens are 22-char URL-safe random strings (V1 default) or owner-supplied vanity slugs (rejected by denylist if abusive).
- Every API route that mutates state requires the token in the path. Cross-endpoint access returns `404` (not `403`) to avoid leaking which `forward_id` or `request_id` values exist under other tokens.

### Observability

- Structured logs (`structlog` JSON) captured by `fly logs`.
- OpenTelemetry traces + metrics exported via OTLP/HTTP. No external backend configured by default — spans go to stdout / fly logs unless `OTLP_ENDPOINT` is set.

### Out of scope by design

- **No PII processing.** hooktrace captures whatever the sender POSTs — owners are responsible for what they expose. The ToS at `/tos` reflects this.
- **No GDPR DSAR endpoint.** Data is deleted 30 days after endpoint creation, no backups, no recovery.
- **No multi-tenancy / accounts.** V5+ territory.
