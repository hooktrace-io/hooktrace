# Claude Code conventions for webhook-inspector

This file is loaded automatically by Claude Code when working in this repo. It documents the conventions the maintainer expects any AI-assisted work to follow.

## Commit messages

- **No `Co-Authored-By: Claude` trailer.** AI assistance is disclosed in the README; we keep it off individual commits.
- [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, `ci:`, `style:`
- Scopes encouraged: `feat(web):`, `fix(infra):`, `test(domain):`
- One commit per logical change. Frequent small commits over one big one.

## Tooling

- **flyctl** (`fly`) for the Fly.io deploys — `fly deploy --config infra/fly/<app>.fly.toml`.
- **uv** for Python deps: `uv add`, `uv sync`, `uv run` — never `pip install` directly.
- **pre-commit** is installed — `git commit` runs ruff format + ruff check + a few hygiene hooks automatically. If a hook reformats, re-stage and re-commit.
- **Make targets**: `make lint`, `make type`, `make test`, `make up`, `make down`, `make migrate`, `make clean`.

> Note: `infra/terraform-legacy/` holds the OpenTofu config from the GCP era — read-only archive.

## Metrics conventions (V2+)

- Use cases depend on `domain/ports/metrics_collector.py:MetricsCollector` ABC, never on OTEL directly.
- Adapter `infrastructure/observability/otel_metrics_collector.py` wraps the OTEL Meter.
- Cardinality is strict — labels limited to `method` (uppercase HTTP verb), `body_offloaded` (bool), `success` (bool). No label may include user-controlled values (token, IP, endpoint_id).
- New metrics go through code review : think about cardinality before adding any label.
- Short-lived jobs (cleaner, migrator) must call `force_flush_metrics()` before exit, or datapoints are lost.
- Heartbeat counters (always +1 per run) are required for absence-based alerts to work reliably.

## Development discipline

- **Test-Driven Development**: write the failing test first, run it to confirm it fails, write the minimum code to pass, run it to confirm it passes, then commit.
- **Clean Architecture layers**: `domain → application → infrastructure → web`. Domain has zero external dependencies. Use cases depend on port interfaces (ABCs), not concrete implementations.
- **Mock the network, not internals**. MSW for frontend, in-memory FakeRepo ABCs for backend use cases. Integration tests hit a real Postgres via testcontainers.
- **Module-level side effects are forbidden** in `web/app/main.py` and `web/ingestor/main.py`. Use the FastAPI `lifespan` async context manager for `Settings()` and observability setup. (Phase C lesson: side effects at import break CI test collection.)

## Infra workflow

- All infra lives under `infra/fly/` as versioned `*.fly.toml` files.
- Three Fly apps in `cdg`: `webhook-inspector-db` (self-managed Postgres), `webhook-inspector-web` (FastAPI + viewer), `webhook-inspector-ingestor` (FastAPI ingestor).
- Postgres was bootstrapped with `fly pg create --name webhook-inspector-db --org personal --region cdg --vm-size shared-cpu-1x --volume-size 10 --initial-cluster-size 1`. **Do not** `fly deploy --config db.fly.toml` — the `flyio/postgres-flex` image expects `FLY_CONSUL_URL` which is only injected by `fly pg create`. `db.fly.toml` is documentation of intent.
- `fly deploy --remote-only --config infra/fly/<app>.fly.toml` for web/ingestor; CI/CD does this on `push: main` via `.github/workflows/deploy.yml` (uses `FLY_API_TOKEN` GitHub secret).
- `release_command = "alembic upgrade head"` in `web.fly.toml` replaces the old Cloud Run Job `migrator` — migrations run automatically before each new web revision is promoted.
- Cleaner is a daily `.github/workflows/cleaner.yml` GH Action that does `flyctl machine run --rm registry.fly.io/webhook-inspector-web:latest -- python -m webhook_inspector.jobs.cleaner`.

## Domain / DNS

- Production domain: `hooktrace.io`, hosted via Cloudflare Registrar.
- Two CNAMEs : `app.hooktrace.io → webhook-inspector-web.fly.dev`, `hook.hooktrace.io → webhook-inspector-ingestor.fly.dev`. Both in **DNS-only mode** (gray cloud), `proxied = false`. TLS is Let's Encrypt managed by Fly.
- The web app derives the hook URL from its own host header (`web/app/routes.py:hook_base_url()`), rewriting `app.` → `hook.` — so the web service **must** stay on `app.<domain>`.

## Observability

- `structlog` for JSON logs, captured directly by `fly logs`. `service.name` field is populated automatically via a logging filter.
- OpenTelemetry traces and metrics export via OTLP/HTTP when `OTLP_ENDPOINT` is set (e.g. Honeycomb). Without it, fallback to `ConsoleSpanExporter` / `ConsoleMetricExporter` (stdout, captured by `fly logs`).
- Headers go in `OTLP_HEADERS` (comma-separated `key=value`), e.g. `x-honeycomb-team=...,x-honeycomb-dataset=webhook-inspector`.
- Old `CLOUD_TRACE_ENABLED` / `CLOUD_METRICS_ENABLED` branches still exist in code but are unused on Fly.

## What this project IS

- A learning side-project to practice modern DevOps (now on Fly.io, was on GCP until 2026-05).
- AI-assisted, transparently — see README disclosure.
- Single env (`dev` acts as prod). No `prod` workspace yet.
- Owner-only — contributions welcome, please open an issue first.

## What this project is NOT

- A commercial product. Free, no signup, URLs expire after 7 days.
- A reference architecture. Choices favor learning over enterprise-grade rigor.
- A multi-env / multi-tenant system (V5+ if ever).

## Roadmap

See README. Current state: **V3 Phase 0 shipped** (2026-05-19) — HMAC validation for 9 services, replay with SSRF guard, per-integration view, forward + retry + DLQ (Upstash Redis + arq worker app), anti-abuse (rate limits + slug denylist + phishing heuristic + Discord notifications), retention bumped 7→30 days, ToS at `/tos`, integration docs at `/docs/integrations`. F4 (schema drift) and F7 (OTEL timeline UI) dropped as low product value. F6 (JSONata transforms) deferred to V4. Next: ops prep (Fly secrets, Cloudflare WAF, R2 lifecycle rule) → Phase 1 customer discovery → soft launch.
