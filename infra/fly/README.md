# Fly.io infra

Four apps deployed in `cdg`:

- `webhook-inspector-db` — self-managed Postgres on a Machine + volume.
- `webhook-inspector-web` — FastAPI app + viewer.
- `webhook-inspector-ingestor` — FastAPI ingestor (webhook receiver).
- `webhook-inspector-worker` — arq worker for async jobs (schema inference, forward).

## Bootstrap from scratch

```bash
cd infra/fly
fly apps create webhook-inspector-db --org personal
fly apps create webhook-inspector-web --org personal
fly apps create webhook-inspector-ingestor --org personal
fly apps create webhook-inspector-worker --org personal
```

Then for each, set secrets and deploy. See `db.fly.toml`, `web.fly.toml`,
`ingestor.fly.toml`.

## Storage

Blobs are stored in a Cloudflare R2 bucket `wi-blobs-prod`. Set the
S3-compatible credentials via `fly secrets set` on `web` and `ingestor`:

```bash
fly secrets set --app webhook-inspector-web \
  S3_ENDPOINT_URL="https://<account>.r2.cloudflarestorage.com" \
  S3_BUCKET_NAME="wi-blobs-prod" \
  S3_ACCESS_KEY_ID="<r2-access-key>" \
  S3_SECRET_ACCESS_KEY="<r2-secret-key>"
```

### R2 lifecycle rule

The `wi-blobs-prod` bucket has a lifecycle rule "delete objects > 31 days
old" configured via the Cloudflare dashboard (Storage → R2 → wi-blobs-prod
→ Settings → Object lifecycle rules). This garbage-collects orphan blobs
left by `Endpoint` / `CapturedRequest` cascade-deletes: the cleaner deletes
DB rows but not R2 objects, so R2-native lifecycle is the right primitive.

Rule:

- Match: all objects (no prefix filter)
- Action: delete after 31 days (1-day grace beyond the 30-day endpoint TTL,
  so a blob never disappears before the row that references it)

If the rule is ever removed, blob storage grows unbounded. Re-add via the
dashboard; no application code path depends on it.

Fallback (not currently implemented): the cleaner could call
`blob_storage.delete(blob_key)` per row before deleting the request
(roughly 10 LOC). Skipped in V3 because R2-native is cheaper and more
reliable than running list-and-delete from a cron.

## Observability — Honeycomb wiring

The 3 apps (web, ingestor, worker) export OpenTelemetry traces + metrics
via OTLP/HTTP when `OTLP_ENDPOINT` is set as a Fly secret. Without it,
they fall back to console output (visible in `fly logs` but with ~30
minute retention only).

### Setup with Honeycomb free tier (20M events/mo)

1. Sign up at https://ui.honeycomb.io/
2. Create an environment (e.g. `production`)
3. Get an API key from Account → Team Settings → API Keys
4. Set the secrets on each app:
   ```bash
   ENDPOINT="https://api.honeycomb.io"
   KEY="<your-honeycomb-api-key>"

   for app in webhook-inspector-web webhook-inspector-ingestor webhook-inspector-worker; do
     fly secrets set --app $app \
       OTLP_ENDPOINT="$ENDPOINT" \
       OTLP_HEADERS="x-honeycomb-team=$KEY,x-honeycomb-dataset=hooktrace"
   done
   ```
5. Restart rolling on each app (Fly does this automatically on secret set).
6. Verify in fly logs: look for `"otlp_tracing_configured"` and
   `"otlp_metrics_configured"` JSON log lines — confirms wiring. The
   fallback lines are `"otlp_tracing_stdout_fallback"` and
   `"otlp_metrics_stdout_fallback"`.
7. In Honeycomb UI, the `hooktrace` dataset should start receiving spans
   within ~1 minute.

### What's exported

- Spans: FastAPI requests + SQLAlchemy queries + custom span (capture_request)
- Metrics: `rate_limit_block_total`, `forward_attempt_total`,
  `request_captured_total`, `body_size` histogram, etc. (see
  `domain/ports/metrics_collector.py` for the full list)
- Resource attributes: `service.name`, `deployment.environment`

### Recommended alerts (set up in Honeycomb)

- p95 capture latency > 500ms over 5 min → warning
- p99 capture latency > 1s over 5 min → critical
- `forward_attempt_total{status="dead"}` rate > 1/min over 5 min → warning
- `rate_limit_redis_error_total` rate > 0 over 1 min → warning (Redis flap)
- `forward_enqueue_failed_total` rate > 5/min over 5 min → critical (queue down)

## Database

`DATABASE_URL` points to the self-managed Postgres via Fly's private mesh:

```
postgresql+psycopg://wi:<password>@webhook-inspector-db.flycast:5432/webhook_inspector
```

## Cleaner

The cleaner runs as a GitHub Actions cron — see `.github/workflows/cleaner.yml`.

## Worker (arq + Redis Upstash)

The worker process consumes the schema-inference queue (PR3) and the forward
queue (PR7+). Async jobs only — no inbound HTTP port.

### One-time provisioning

```bash
# Redis (Upstash plan) — outputs a rediss:// (TLS) DSN
fly redis create

# App + secrets
fly apps create webhook-inspector-worker --org personal
fly secrets set --app webhook-inspector-worker \
  REDIS_URL="rediss://default:<pw>@<id>.upstash.io:6379" \
  DATABASE_URL="postgresql+psycopg://wi:<pw>@webhook-inspector-db.flycast:5432/webhook_inspector" \
  SECRETS_ENCRYPTION_KEY="<base64-32-bytes>" \
  S3_ENDPOINT_URL="https://<account>.r2.cloudflarestorage.com" \
  S3_BUCKET_NAME="wi-blobs-prod" \
  S3_ACCESS_KEY_ID="<r2-access-key>" \
  S3_SECRET_ACCESS_KEY="<r2-secret-key>"

# Deploy
fly deploy --remote-only --config infra/fly/worker.fly.toml
fly status --app webhook-inspector-worker  # machine running, no health (no http_service)
fly logs --app webhook-inspector-worker    # should show `worker_startup` JSON line
```

### Notes

- **`rediss://` is mandatory.** Upstash refuses non-TLS connections on the hosted offering.
- **No `http_service`** in `worker.fly.toml` — Fly's process supervisor handles liveness ("did the process exit?"). PR10 will add alerting on Redis queue depth as the real liveness signal.
- **No `release_command`** — web's `release_command = "alembic upgrade head"` runs on every push and the worker is stateless w.r.t. migrations.
- The worker shares the same Dockerfile as web/ingestor ; `arq` is on PATH via the venv.
