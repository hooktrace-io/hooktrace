# hooktrace

[![CI](https://github.com/hooktrace-io/hooktrace/actions/workflows/lint-and-test.yml/badge.svg)](https://github.com/hooktrace-io/hooktrace/actions/workflows/lint-and-test.yml)
[![Deploy](https://github.com/hooktrace-io/hooktrace/actions/workflows/deploy.yml/badge.svg)](https://github.com/hooktrace-io/hooktrace/actions/workflows/deploy.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/release/python-3130/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type checked: mypy](https://img.shields.io/badge/types-mypy_strict-blue.svg)](https://mypy.readthedocs.io/)

**The free observability layer for webhooks.** Capture, validate HMAC signatures (Stripe, GitHub, Shopify, Twilio, Mailgun, Discord, Slack, Zapier, n8n), replay to your dev server via an ngrok / Cloudflare Tunnel, forward with retry + DLQ — all from one anonymous URL. No signup, endpoints expire 30 days after creation.

> **AI-assisted development.** Parts of this codebase were drafted with Claude (Anthropic) acting as a pair programmer. All design decisions, architectural reviews, debugging, and verification are mine.

## Architecture

```
                       ┌──────────────────────┐
                       │  Cloudflare DNS      │
                       │  app.hooktrace.io    │
                       │  hook.hooktrace.io   │
                       └──────────┬───────────┘
                                  │
              ┌───────────────────┴────────────────────┐
              ▼                                        ▼
      ┌──────────────┐                         ┌──────────────┐
      │  Fly Machine │                         │  Fly Machine │
      │  "web"       │                         │  "ingestor"  │
      │              │                         │              │
      │  FastAPI +   │                         │  FastAPI +   │
      │  Jinja2 +    │                         │  HMAC check  │
      │  HTMX + SSE  │                         │  + body limit│
      └──────┬───────┘                         └──────┬───────┘
             │                                        │
             │   ┌────────────────────────────────────┤
             │   │                                    │
             ▼   ▼                                    ▼
      ┌──────────────────┐                  ┌──────────────────┐
      │  Fly Postgres    │  LISTEN/NOTIFY   │  Cloudflare R2   │
      │  (self-managed)  │  ◄────────────►  │  body offload    │
      │  Postgres 16     │                  │  > 8 KB          │
      └──────────────────┘                  └──────────────────┘
             ▲    ▲
             │    │
             │    └──── claim/update forwards ────┐
             │                                    │
             │                                    │
             │            enqueue + cron          │
        ┌────┴──────────┐  ┌──────────────┐  ┌────┴─────────┐
        │  Upstash      │◄─┤  Fly Machine │  │  GH Actions  │
        │  Redis        │  │  "worker"    │  │  cron        │
        │               │  │              │  │  "cleaner"   │
        │  arq queue +  │  │  arq runner  │  │  daily 03:00 │
        │  rate-limit   │  │  forwards +  │  │  UTC         │
        │  sliding win  │  │  abuse_scan  │  └──────────────┘
        └───────────────┘  │  cron 03:30  │
                           └──────┬───────┘
                                  │
                                  ▼
                          ┌────────────────────────┐
                          │ OTLP/HTTP → Honeycomb  │
                          │ (traces + metrics)     │
                          │ + structlog → fly logs │
                          └────────────────────────┘
```

**Three FastAPI / arq services + one scheduled job sharing one Python package:**

- `web` — viewer UI + REST API + SSE stream (Fly Machine, min=1 for warm SSE)
- `ingestor` — public webhook capture (Fly Machine, autoscaled)
- `worker` — arq job runner: forward jobs + `abuse_scan` cron (Fly Machine)
- `cleaner` — daily GitHub Actions cron, deletes expired endpoints via `flyctl machine run --rm`
- Migrations run as `release_command = "alembic upgrade head"` before each web revision is promoted

Stack: Python 3.13 + FastAPI + SQLModel + `arq` + Fly Machines + self-managed Fly Postgres + Upstash Redis + Cloudflare R2 + Cloudflare WAF + OpenTelemetry (OTLP/HTTP → Honeycomb) + GitHub Actions.

The data flow on a webhook capture:

1. Client POSTs to `https://hook.hooktrace.io/h/{token}`
2. Rate-limit middleware (100 req/min/IP on `/h/`, sliding window in Redis) — **fail-closed** if Redis is unavailable: capture surface is the abuse vector, we'd rather 503 than drop the gate
3. `ingestor` looks up the endpoint, captures method/headers/body/source IP
4. HMAC signature validation against the endpoint's configured provider (if any) — result persisted as `signature_status` (`valid` / `invalid` / `missing`)
5. Integration auto-detection from headers + body shape — persisted as `detected_integration` (`stripe`, `github`, `shopify`, `twilio`, `mailgun`, `discord`, `slack`, `zapier`, `n8n`, or `null`)
6. Bodies > 8 KB offloaded to Cloudflare R2, smaller ones inline in Postgres
7. INSERT + `pg_notify('new_request', '...')` in one atomic transaction
8. If the endpoint has a `forward_url` configured, enqueue an `execute_forward` job on Redis for the worker to deliver with retry + backoff
9. `web`'s SSE handlers listening on Postgres NOTIFY receive the request_id and push an HTML fragment to every matching open `/stream/{token}` connection
10. HTMX in the browser inserts the fragment at the top of the live list

## Quick start (local)

Requires Docker + docker-compose.

```bash
make up
# wait ~10s for migrate to complete

# Create an endpoint
TOKEN=$(curl -sX POST http://localhost:8000/api/endpoints | python -c "import sys,json;print(json.load(sys.stdin)['token'])")

# Send a webhook
curl -X POST -d '{"hello":"world"}' http://localhost:8001/h/$TOKEN

# Watch it live
open http://localhost:8000/$TOKEN
```

## Development

```bash
make install   # uv sync
make lint      # ruff
make type      # mypy
make test      # full pytest suite
make up        # full docker-compose stack
make clean     # run cleaner job manually
```

For faster iteration with hot reload:

```bash
make dev-postgres
# In a second terminal:
export DATABASE_URL=postgresql+psycopg://webhook:webhook@localhost:5434/webhook_inspector
make dev-app
# In a third terminal:
make dev-ingestor
```

This runs the FastAPI services locally with `uvicorn --reload` so code changes take effect without rebuilding Docker images.


## Production deployment

Live URLs:
- App: `https://app.hooktrace.io`
- Ingestor (webhook target): `https://hook.hooktrace.io`

Generated webhook URLs (`POST /api/endpoints`) automatically point to the ingestor subdomain. Use as-is in any service that sends webhooks (Stripe, GitHub, Slack...).

Deploys are automatic on push to `main` via `.github/workflows/deploy.yml` (`flyctl deploy --remote-only` on each service). See `infra/fly/README.md` for the deployment topology.

Trace data is exported via OTLP/HTTP — point `OTLP_ENDPOINT` at any OTLP backend (Honeycomb, Grafana Cloud, etc.) and traces ship there. Without `OTLP_ENDPOINT`, spans go to stdout and are visible in `fly logs`.

## Custom response

By default a captured webhook gets `200 OK` with body `{"ok":true}`. You can customize this when creating an endpoint:

```bash
curl -X POST https://app.hooktrace.io/api/endpoints \
  -H 'Content-Type: application/json' \
  -d '{
    "response": {
      "status_code": 201,
      "body": "{\"created\":true}",
      "headers": {"Content-Type": "application/json"},
      "delay_ms": 0
    }
  }'
```

Constraints:
- `status_code` in `[100, 599]`
- `delay_ms` in `[0, 30000]`
- `body` up to 64 KiB
- `headers` cannot override `Content-Length`, `Transfer-Encoding`, `Connection`, `Host`, `Date`

You can also configure all of this via the landing page's "Advanced options" disclosure.

## Vanity URL slug

```bash
curl -X POST https://app.hooktrace.io/api/endpoints \
  -H 'Content-Type: application/json' \
  -d '{"slug": "my-stripe-test"}'
```

Constraints:
- 3–32 chars, lowercase letters / digits / hyphens, no leading/trailing hyphen
- Reserved slugs (`api`, `health`, `stripe`, `github`, …) return 400 — see the brand denylist in `slug_denylist.py`
- Already-claimed slugs return 409

Without `slug`, you still get a random 22-char token (V1 behavior).

## HMAC signature validation

Configure the endpoint's signature provider once, then every captured request shows a green / red / gray badge indicating signature validity. Nine providers supported out of the box (Stripe, GitHub, Shopify, Twilio, Mailgun, Discord, Slack, Zapier, n8n).

```bash
curl -X PATCH "https://app.hooktrace.io/api/endpoints/$TOKEN/config" \
  -H 'Content-Type: application/json' \
  -d '{"signature": {"provider": "stripe", "secret": "whsec_..."}}'
```

The secret is encrypted at rest with the `SECRETS_ENCRYPTION_KEY`. The check result is persisted as `signature_status = valid | invalid | missing`. See [`docs/integrations/`](src/webhook_inspector/docs/integrations/README.md) for each provider's signing scheme (header name, hash algorithm, payload composition).

## Replay

Click any captured request in the viewer to POST it to a public URL — a staging backend, or an `ngrok` / Cloudflare Tunnel pointing at your local dev server. The SSRF guard rejects loopback, RFC1918 private ranges, link-local, and cloud metadata endpoints (`169.254.169.254`, `100.64.0.0/10`, `fd00::/8`, …), so the target must resolve to a public address — replay directly at `http://localhost:3000` will be refused, by design.

```bash
curl -X POST "https://app.hooktrace.io/api/endpoints/$TOKEN/requests/$REQUEST_ID/replay" \
  -H 'Content-Type: application/json' \
  -d '{"target_url": "https://my-backend.com/webhook"}'
```

Per-token rate limit: 10 replays per hour (fail-open if Redis is down — replays are an authenticated convenience surface, not an abuse vector).

## Per-integration view

Auto-detection classifies each captured request from headers + body shape. The viewer's `/{token}/integrations` page (and its REST endpoint) returns counters, p95 latency, and signature error rate per integration.

```bash
curl "https://app.hooktrace.io/api/endpoints/$TOKEN/integrations"
# → [{"integration":"stripe","count":47,"signature_errors":2,"p95_ms":118}, …]
```

## Forward with retry + DLQ

Relay every captured request to your prod or staging backend. Outbound POSTs are signed `X-Hooktrace-Signature: t=…,v1=…` (HMAC-SHA256, Stripe-compatible — receivers can reuse their Stripe verification code with a different secret).

```bash
curl -X PATCH "https://app.hooktrace.io/api/endpoints/$TOKEN/config" \
  -H 'Content-Type: application/json' \
  -d '{"forward": {"url": "https://api.your-backend.com/webhook", "secret": "whsec_..."}}'
```

Retry schedule: 4 retries at 30 s → 2 m → 10 m → 1 h, then `dead`. Retryable failures: network errors, 408, 425, 429, 5xx. Hard 4xx (404, 422, …) go straight to `dead` — your handler said no, we don't replay forever.

Failed and dead forwards land in the DLQ at `/{token}/forwards` with Retry / Abandon / Redrive actions. The contract for the outbound signature header, idempotency key, and retry semantics is documented at [`docs/integrations/verifying-forwards.md`](src/webhook_inspector/docs/integrations/verifying-forwards.md).

## Anti-abuse

- **Capture rate limit** : 100 req/min/IP on `/h/`, sliding window in Upstash Redis, **fail-closed** (503) — capture is the public abuse vector.
- **Per-token caps** : capture 1 000 req/h/endpoint, replay 10 req/h/endpoint. **Fail-open** if Redis is unavailable (authenticated surfaces — degrade gracefully).
- **Slug denylist** : brand names (`stripe`, `paypal`, `revolut`, …) and admin-reserved slugs (`admin`, `root`, `health`, …) return 400 on `POST /api/endpoints`. Prevents the most obvious phishing setup.
- **Phishing heuristic** : daily `abuse_scan` arq cron (03:30 UTC) flags endpoints with high POST traffic but no successful forwards (no human is reading them — likely a credential capture sink). Flagged endpoints retain standard 30 d retention (no auto-freeze); the maintainer reviews manually via a Discord webhook notification.
- See [`SECURITY.md`](SECURITY.md) for the full threat model.

## Search captured requests

```bash
curl "https://app.hooktrace.io/api/endpoints/$TOKEN/requests?q=payment_intent.succeeded"
```

Searches across method, path, body (first 8 KB), and headers. Powered by Postgres `tsvector` + GIN index.

Notes / limitations:
- **AND semantics**: `q=foo bar` matches rows containing BOTH `foo` AND `bar` (any order). Not phrase search.
- **Hyphenated tokens split**: the `simple` tsearch config tokenizes on `-`, so `x-stripe-signature` is indexed as three tokens (`x`, `stripe`, `signature`). Search the full header name and you'll match via AND.
- **Slash-prefixed paths kept whole**: `/health` is indexed as the single token `'/health'` (the leading `/` is preserved). Search for `/health` (with the slash) to match it; bare `health` won't unless it also appears in body/headers.
- **8 KB body cap**: bodies offloaded to R2 (> 8 KB) aren't searchable.
- **Live updates don't honor active search**: requests captured during a search session aren't auto-filtered. Re-submit the query to refresh.

## Export captured requests

```bash
curl -OJ "https://app.hooktrace.io/api/endpoints/$TOKEN/export.json"
```

Streams a single JSON file with full bodies (including bodies offloaded to R2, fetched on-the-fly). Cap: 10 000 requests per export (`EXPORT_MAX_REQUESTS` env override). Beyond the cap returns 413.

Response format:

```json
{
  "endpoint": {
    "token": "my-stripe-test",
    "created_at": "...",
    "expires_at": "...",
    "response": { "status_code": 200, "body": "...", "headers": {}, "delay_ms": 0 }
  },
  "exported_at": "...",
  "exported_request_count": 142,
  "requests": [
    {
      "id": "...",
      "method": "POST",
      "path": "/",
      "headers": {...},
      "body": "...full body, inlined from DB or fetched from R2...",
      "body_size": 1234,
      "received_at": "..."
    }
  ]
}
```

`requests` are ordered most-recent-first. `exported_request_count` is the count of rows in the array, not the endpoint's lifetime counter.

## Roadmap

| Phase | Status | Focus |
|-------|--------|-------|
| V1 | ✅ Live | MVP : 5 endpoints + live viewer + Cloud Run + WIF CI/CD + custom domain + Cloud Trace |
| V2 | ✅ Live | Custom response (status/body/headers/delay) + copy-as-curl + custom OTEL metrics + Cloud Monitoring dashboards + alerting |
| V2.5 | ✅ Live | **UX produit** — vanity URL slug + search/filter (Postgres `tsvector` + GIN index) + export captured requests as JSON |
| V2.6 | ✅ Live | **Migration cloud** — GCP (Cloud Run + Cloud SQL + GCS + Cloud Trace) → Fly.io (Machines + self-managed Postgres + Cloudflare R2) + OTLP traces. |
| V3 | ✅ Live | **Observability pivot** — HMAC validation built-in (9 providers) + per-integration view + replay with SSRF guard + forward with retry + DLQ + anti-abuse (rate limits, slug denylist, phishing heuristic, abuse-scan cron) + retention bumped 7 → 30 days |
| V4 | 🟡 Planned | **Production hardening** — multi-region read replicas + HA Postgres pair + formal SLOs + transform JSONata (Pro) + multi-target fan-out (Team) |
| V5 | 🟡 Planned | **Auth + power user** — Google OAuth + claimed URLs + activity log per-account + statistics charts + API tokens + (optional) DNSBL lookup |
| V6 | 🟡 Planned | Formal SLOs + error budgets + status page publique + first real postmortem |
| V7+ | 🟡 Future | WebSocket inspection (new protocol dimension) + SMTP/email capture (new service infra) — explored as desire dictates |

V3 dropped two items vs. the original plan: schema-drift detection (F4) and an OTEL timeline UI (F7) — both removed for low actionable value. The JSONata transforms feature (F6) is deferred to V4. See [`docs/specs/`](docs/specs/) for design rationale.

## Contributing

Contributions welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md). For security issues, please use [GitHub Security Advisories](https://github.com/hooktrace-io/hooktrace/security/advisories/new) (see [`SECURITY.md`](SECURITY.md)). For per-provider signature schemes and the outbound forward contract, see [`docs/integrations/`](src/webhook_inspector/docs/integrations/README.md).

## License

[MIT](LICENSE) © 2026 Stanislas Plum
