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

## Observability

Traces and metrics go to Honeycomb via OTLP. Set `OTLP_ENDPOINT` and
`OTLP_HEADERS` per app:

```bash
fly secrets set --app webhook-inspector-web \
  OTLP_ENDPOINT="https://api.honeycomb.io" \
  OTLP_HEADERS="x-honeycomb-team=<honeycomb-api-key>,x-honeycomb-dataset=webhook-inspector"
```

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
