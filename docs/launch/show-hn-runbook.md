# Show HN launch runbook — hooktrace.io

Three phases — pre-launch (J-7 to J-1), day J, post-launch (J+1 to J+7).
Drafted from the V3 Phase 0 plan, updated 2026-05-20 after the
observability cleanup pass (PRs #56-#61).

---

## Phase 1 — Pre-launch

### J-7: Verify the product is live

Everything below must work for anyone who lands on the site cold. Run
each step; if any one fails, fix before posting.

```bash
# 1. Landing under 2s
curl -s -o /dev/null -w "%{time_total}s\n" https://app.hooktrace.io/

# 2. CTA: POST /api/endpoints → token under 1s
time curl -s -X POST https://app.hooktrace.io/api/endpoints | jq -r .token

# 3. Capture: POST /h/{token} → 200 under 500ms
TOKEN=<from previous>
time curl -s -X POST https://hook.hooktrace.io/h/$TOKEN -d '{"hello":"world"}'

# 4. Viewer shows the row
open https://app.hooktrace.io/$TOKEN

# 5. Replay — in the viewer, click Replay → https://httpbin.org/post → success toast

# 6. Forward — PATCH /config with forward.url → POST capture → /{token}/forwards shows succeeded

# 7. Integration view — POST 3-5 times with `-H "Stripe-Signature: t=$(date +%s),v1=demo"` → /{token}/integrations groups under "stripe" (detector keys on provider headers, NOT User-Agent — see J-5 seed snippet for the GitHub/Shopify equivalents)

# 8. HMAC validation — configure stripe secret → POST with valid Stripe-Signature → green pill in viewer

# 9. Documentation pages render
curl -s -o /dev/null -w "%{http_code}\n" https://app.hooktrace.io/docs/integrations
curl -s -o /dev/null -w "%{http_code}\n" https://app.hooktrace.io/docs/integrations/verifying-forwards
curl -s -o /dev/null -w "%{http_code}\n" https://app.hooktrace.io/tos
# all three: 200 (verified 2026-05-20)
```

### J-5: Prepare the visuals

Two screenshots are mandatory for the HN body:

1. **Viewer with auto-detected integrations.** Seed it with realistic
   traffic:

   The integration detector keys on provider-specific headers, NOT
   User-Agent (see `src/webhook_inspector/domain/services/integration_detector.py`).
   Seed with the right headers or the pills won't appear:

   ```bash
   TOKEN=$(curl -s -X POST https://app.hooktrace.io/api/endpoints | jq -r .token)
   HOOK="https://hook.hooktrace.io/h/$TOKEN"

   # Stripe: needs Stripe-Signature
   for i in 1 2 3; do
     curl -s -X POST "$HOOK" \
       -H "Stripe-Signature: t=$(date +%s),v1=demo" \
       -H "Content-Type: application/json" \
       -d "{\"type\":\"charge.succeeded\",\"id\":\"evt_test_$i\"}" -o /dev/null
   done

   # GitHub: needs BOTH x-github-event AND x-github-delivery
   for i in 1 2 3; do
     curl -s -X POST "$HOOK" \
       -H "X-GitHub-Event: push" \
       -H "X-GitHub-Delivery: $(uuidgen)" \
       -H "Content-Type: application/json" \
       -d "{\"ref\":\"refs/heads/main\",\"head_commit\":{\"id\":\"sha$i\"}}" -o /dev/null
   done

   # Shopify: needs x-shopify-topic AND x-shopify-shop-domain
   for i in 1 2 3; do
     curl -s -X POST "$HOOK" \
       -H "X-Shopify-Topic: orders/create" \
       -H "X-Shopify-Shop-Domain: demo.myshopify.com" \
       -H "Content-Type: application/json" \
       -d "{\"id\":$i,\"total_price\":\"42.00\"}" -o /dev/null
   done
   ```

   Then screenshot `https://app.hooktrace.io/$TOKEN` — Stripe/GitHub/Shopify
   rows with their integration pill. Sells the differentiator (HMAC
   validation + per-integration detection in the same view).

2. **DLQ page** (`/{token}/forwards`) — status counters + Retry buttons
   + attempt counts. Demonstrates "not just an inspector".

Upload anonymously to Imgur (https://imgur.com — no account needed).
Keep the direct links.

**Optional but strong**: a 10-15s GIF of "create URL → curl POST → row
appears live". Tools: Kap (https://getkap.co/, macOS) or LICEcap
(https://www.cockos.com/licecap/, cross-platform). Upload to Imgur.

### J-5: Honeycomb dashboard — already done

Board: **"Hooktrace Launch — HN"**
URL: https://ui.honeycomb.io/hooktrace/environments/test/board/zhdeEnJdNU3

10 panels covering engagement, pipeline, reliability, abuse. 2 panels
(forward enqueue failures, SSRF blocks) are intentionally skipped —
their underlying metrics have never fired in prod, so Honeycomb hasn't
created the columns. They'll come back on the next `uv run python
/tmp/honeycomb_apply.py` after the first incident.

The legacy "Show HN live monitoring" and "Forward delivery" boards from
earlier were broken (queried `service.name=ingestor` inside the app
dataset, used OTEL `status_code` instead of `http.status_code`) — they
were deleted in the same script run.

### J-3: Draft the HN post

**Title** (71 chars, under HN's 80 soft cap):

```
Show HN: Hooktrace – webhook observability with HMAC validation, replay, forward
```

**Body** (posted as the first comment 30s after submitting the link —
HN convention for Show HN, gets `[OP]` tag):

```
Hi HN, I built hooktrace because I kept hitting the same workflow when
debugging Stripe + GitHub webhooks: capture in webhook.site, copy the
payload to a curl command, fire it at my local backend, repeat. Each
step lost context (was the HMAC signature valid? what's the actual
event type? did Stripe send 3 retries or 1?).

hooktrace is one URL that captures + shows the requests live (like
webhook.site), AND:

- Built-in HMAC signature validation for 9 services (Stripe, GitHub,
  Shopify, Twilio, Mailgun, Discord, Slack, Zapier, n8n) — green/red
  pill next to each row showing whether YOUR secret validates the
  incoming signature
- Replay any captured request to a public staging URL or a tunnel
  (ngrok / Cloudflare Tunnel pointing at localhost) — replay is
  SSRF-guarded so loopback and RFC1918 targets are rejected by design
- Forward all webhooks to your prod backend with exponential retry +
  DLQ — useful for "bridge Stripe dev webhooks to staging environment"
- Auto-detects which integration sent each request and shows
  per-integration counters

It's free, no signup, anonymous URLs, 30-day retention. Built with
FastAPI + SQLAlchemy 2.0 async + arq + Fly.io + Cloudflare R2 +
Honeycomb. Open source, MIT.

Repo:        https://github.com/hooktrace-io/hooktrace
Live:        https://app.hooktrace.io
Docs:        https://app.hooktrace.io/docs/integrations
Outbound:    https://app.hooktrace.io/docs/integrations/verifying-forwards

Screenshots:
- Viewer with auto-detected integrations: <IMGUR_LINK_1>
- DLQ management for failed forwards:     <IMGUR_LINK_2>

Happy to answer questions about the architecture, the WAF/rate-limit
defense layers, or the GCP→Fly migration we did in May.
```

### J-3: Anticipated Q&A

Print this section or open it in a side tab on launch day.

#### Q1: How is this different from webhook.site?

webhook.site captures and shows. hooktrace does that AND:
1. **HMAC validation built-in** — paste your Stripe webhook secret once,
   every incoming request gets a green/red pill showing whether the
   signature matches. Webhook.site doesn't validate, you have to copy
   the body into a curl + your own verification script.
2. **Forward + DLQ + retry budget** — feed prod webhooks to staging
   continuously with retry-on-failure. Webhook.site has forwarding but
   not retry/DLQ semantics.
3. **Free** at higher caps. Webhook.site free tier is 100 reqs total
   then paywalled.

#### Q2: Open source? Self-hostable?

Yes — MIT license. Repo at github.com/hooktrace-io/hooktrace.
`make up` for local dev (docker-compose). Self-host docs are not
written yet (V4) but the prod deployment is just 3 Fly apps + Postgres +
Redis — anyone with terraform/fly literacy can reproduce it.

#### Q3: Won't tokens get guessed / brute-forced?

Random tokens: `secrets.token_urlsafe(16)` = 16 bytes = **128 bits of
entropy** (URL-safe encoding doesn't add entropy, just changes the
charset). Vanity slugs (3-32 chars lowercase) are weaker — explicit
user choice. Slug denylist blocks brand names + admin reserved.

Per-IP rate limits: **100/min** on `/h/` (ingestor, fail-closed),
**300/min** on `/api/endpoints/` (app, fail-open).

Per-token caps: **1000 captures/h** on the ingestor, **10 replays/h**
on the app. Both verified in `web/ingestor/routes.py:CAPTURE_LIMIT_PER_HOUR`
and `web/app/routes.py:REPLAY_LIMIT_PER_HOUR`.

#### Q4: SSRF on replay/forward?

Two-layer parse-time guard in `infrastructure/http/safe_replay_target.py`:
1. URL parse + scheme allowlist (http/https only) + port allowlist (80/443)
2. DNS resolution + RFC1918/loopback/link-local/cloud-metadata rejection
   on the resolved IPs. Also `follow_redirects=False` on the httpx call —
   a public URL can 301 to a private one, this would bypass step 2
   otherwise.

**Known V3 gap, documented in the module docstring:** classic DNS-rebinding
TOCTOU. validate() resolves once at parse time, httpx re-resolves at
connect time. An attacker-controlled DNS record that swaps to 127.0.0.1
between the two lookups would still connect to loopback. The fix
(pin the connection to the validated IP, or resolve+dial yourself) is
on the V4 list. For a side-project where the SSRF target has no
ambient credentials and Fly's private network is operator-only, the
risk is "an attacker can probe Fly's loopback from our worker" — not
zero, but bounded.

#### Q5: Abuse handling?

Layered:
1. Cloudflare WAF (custom rules + Bot Fight Mode)
2. Per-IP rate limit at the ingestor (100/min, fail-closed)
3. Per-token caps (capture 1000/h, replay 10/h — see Q3)
4. Slug denylist (32 reserved substrings)
5. Daily abuse_scan cron (03:30 UTC) — flags endpoints with high POST
   traffic but no successful forward (phishing harvest via viewer page)
6. AES-256-GCM encryption for HMAC + forward secrets at rest

#### Q6: Why FastAPI + Fly + R2 (not Cloud Run + S3)?

Was on GCP until May 2026. Cloud Run + Cloud SQL was the wrong shape
for this workload: Cloud SQL bills 24/7 for a database that's idle
~90% of the time, and Cloud Run's per-request billing was dominated
by the per-instance minimum. Fly's small-machine + autosuspend pricing
maps directly to the actual usage pattern — pay for what's running,
nothing for what isn't.

R2 vs S3 specifically: 10GB free + **zero egress fees**. For a tool
whose viewer streams captured bodies back to a browser, egress
dominates the bill on S3 ($0.09/GB) and is free on R2.

(If asked for concrete numbers, see the migration plan referenced from
the README — exact line items are in there.)

#### Q7: Business model?

Side project. Free tier permanent. No monetization plan today. If infra
costs ever become real I'd add a "Pro tier" for longer retention +
more endpoints — but the free tier stays. MIT source = self-host
escape hatch.

#### Q8: Load tested?

k6 script under `load/capture.js`. **Local: p95 = 427ms** at ~250 req/sec
sustained (laptop, docker-compose stack). **Production: p95 = 90ms**
under the rate-limit ceiling for a single source IP (Cloudflare edge +
ingestor on shared-cpu-1x).

These are the numbers I'd quote on launch day. If you want a fresh run
the morning of, re-execute `k6 run load/capture.js` against prod and
update this section — but the numbers above are real and within the
last week.

#### Q9: What's missing? What would you NOT use this for?

- **No auth.** Anyone with the URL is the owner. Lose URL = lose access.
- **Single-target forward.** V3 forwards to one URL. Multi-fanout = V4.
- **No batch replay.** V3.5.
- **30-day retention, no SLA.** Self-managed Postgres single machine,
  no replication. No user-facing recovery guarantee — Fly's auto-volume
  snapshots (5-day retention, operator-only) exist as an infra safety
  net but are not exposed as a product feature.
- **Not for production-critical traffic.** It's debug/dev infrastructure.

#### Q10: Why no signup?

Friction kills the use case. webhook.site is dominant precisely because
it's "paste URL → go". Requiring signup means losing to Hookdeck on
polish since they've invested years there. Trade-off accepted: the
token IS the credential.

#### Q11: How does it compare to Hookdeck / Svix?

- **Hookdeck**: production-grade, signup-required, $20/mo entry. Excellent
  for committed webhook infrastructure. hooktrace is for the "I need
  a URL to debug Stripe right now" use case.
- **Svix**: similar to Hookdeck, slightly different positioning (sending
  webhooks rather than receiving). hooktrace doesn't compete there.

#### Q12: Why these 9 integrations and not PayPal?

The 9 are all HMAC-symmetric (HMAC-SHA256 or SHA1). PayPal is
RSA-SHA256 with a cert chain — cert caching, chain validation, expiry
handling = materially more work. Deferred to V3.5. Stripe Connect uses
the same `Stripe-Signature` header so it's covered today.

#### Q13: Accuracy of integration auto-detection?

Header-based first (provider-specific headers like `Stripe-Signature`,
`X-Hub-Signature-256`), UA-based fallback for senders without unique
headers. False positives possible if a custom service mimics a
provider's header. Code:
`src/webhook_inspector/domain/services/integration_detector.py`.

#### Q14: Plans for X (batch replay / websocket / SMTP)?

V7+ for protocol-level additions. Not soon. Open an issue if it matters
to you.

#### Q15: Security disclosure?

Private security advisories:
https://github.com/hooktrace-io/hooktrace/security/advisories/new
Please don't open a public issue.

#### Q16-20: Feature requests / docs / bug reports

Default response: _"Thanks — open an issue and I'll triage."_ Or:
_"Known limitation, on the roadmap — link to issue X."_

### J-1: Final check

```bash
# 1. CI green on main — check all 4 workflows
gh run list --workflow=lint-and-test --limit 1 --json conclusion --jq '.[0].conclusion'
gh run list --workflow=deploy        --limit 1 --json conclusion --jq '.[0].conclusion'
gh run list --workflow=trivy         --limit 1 --json conclusion --jq '.[0].conclusion'
gh run list --workflow=codeql        --limit 1 --json conclusion --jq '.[0].conclusion'
# expect: success success success success

# 2. No critical open PR
gh pr list --state open
# if anything is open: defer post-launch, no debugging during HN

# 3. Fly machines reachable
for app in webhook-inspector-web webhook-inspector-ingestor webhook-inspector-worker webhook-inspector-db; do
  echo "=== $app ==="
  fly status -a $app | grep -E "started|stopped|Image"
done

# 4. Honeycomb is receiving
curl -s -X POST https://hook.hooktrace.io/h/$(curl -s -X POST https://app.hooktrace.io/api/endpoints | jq -r .token) -d '{"j-1":"smoke"}'
# Open https://ui.honeycomb.io/hooktrace/environments/test/board/zhdeEnJdNU3
# Verify capture rate panel ticked up

# 5. Discord webhook works
# (send a test message manually in the operator channel)
```

---

## Phase 2 — Day J

### Timing

**Tuesday, Wednesday, or Thursday.** Not Monday (backlog catch-up).
Not Friday (weekend kills the thread). Avoid US holidays (Memorial Day,
July 4th, Thanksgiving, Christmas week).

**14:00 UTC** sweet spot (May, DST active — BST = UTC+1, CEST = UTC+2,
EDT = UTC-4, PDT = UTC-7):
- US East morning (10:00 EDT): East Coast café wave, the dominant HN
  cohort — this is the primary target window
- Europe afternoon (15:00 BST / 16:00 CEST): post-lunch dip, lower
  signal but still active
- Leaves ~7h of active moderation before US West close (17:00 PDT)

If your priority is Europe morning (10:00 CEST = 08:00 UTC), the US
East side will only catch the early-bird 04:00 EDT crowd — worse
trade-off for Show HN. Stay at 14:00 UTC unless you have a Europe-only
angle.

### 1h before — 13:00 UTC (09:00 EDT / 14:00 BST / 15:00 CEST)

Open these tabs:

1. https://news.ycombinator.com/submit
2. https://news.ycombinator.com/ (watch ranking)
3. **The Honeycomb board**: https://ui.honeycomb.io/hooktrace/environments/test/board/zhdeEnJdNU3
4. Discord operator channel (abuse alerts land here)
5. This file — for the Q&A section
6. Three terminals:
   - `fly logs -a webhook-inspector-web`
   - `fly logs -a webhook-inspector-ingestor`
   - `fly logs -a webhook-inspector-worker`
7. GitHub repo (star counter)
8. https://app.hooktrace.io/ (verify it stays up under load)

Last sanity check:

```bash
curl -s -o /dev/null -w "app:      %{http_code}\n" https://app.hooktrace.io/
curl -s -o /dev/null -w "ingestor: %{http_code}\n" https://hook.hooktrace.io/health
```

### 14:00 UTC — Post

1. https://news.ycombinator.com/submit
2. **Title**: `Show HN: Hooktrace – webhook observability with HMAC validation, replay, forward`
3. **URL**: `https://app.hooktrace.io`
4. **Text**: leave empty (the body goes in a comment — HN Show HN convention)
5. Submit.

### 14:00:30 UTC — First comment

Immediately click your own post → "add comment" → paste the body from
the J-3 section. Single action, gets you `[OP]` tag.

### 14:00–14:30 — First half hour

- Refresh your post every 5 min for the first comments
- "Show HN" front-page appearance on https://news.ycombinator.com/show
  = signal you're climbing
- 0 upvotes after 30 min beyond your own = bad timing or weak title.
  Retry in 2-3 months with a different angle. Don't delete; just let it
  fade.

### 14:30–20:00 — Moderation

- Reply to every comment within 30 min
- Adapt the Q&A bank above to the specific question
- Stay calm on hostile comments
- Cite code/docs by path (e.g.
  `src/webhook_inspector/domain/services/integration_detector.py`)
- Don't engage in philosophy threads (future of webhooks, monetization
  ethics, etc.)

### Production monitoring (while moderating)

Honeycomb board panels to glance at every 10 min:
- **Capture latency heatmap** — outliers visible? p95 spike?
- **Ingestor 5xx** — any non-zero = visible failures for posters
- **Forward outcomes** — failed/dead rate climbing?
- **Rate limit blocks** — climbing is expected from HN traffic; sustained
  means we should consider raising 100/min temporarily

Fly logs to grep for:
- Unhandled Python exceptions
- 503s from the rate-limit fail-closed path (= Redis flap)
- Memory pressure (slow GC pauses on shared-cpu-1x)

Discord:
- abuse_scan at 03:30 UTC will flag HN-created endpoints that received
  >20 POSTs without a configured forward → **expected, not a problem**.

### If something breaks under load

**Don't panic.** Fly rollback is fast:

```bash
fly releases -a webhook-inspector-web
fly releases rollback -a webhook-inspector-web <ID>
# repeat for -ingestor / -worker as needed
```

Post in the thread publicly: _"Heads up — seeing X under load, rolling
back to last stable. Update in 10 min."_ HN respects honest technical
disclosure during incidents.

---

## Phase 3 — Post-launch

### J+1 morning

1. Sweep overnight comments
2. KPIs:

   ```bash
   # Stars
   curl -s https://api.github.com/repos/hooktrace-io/hooktrace | jq .stargazers_count

   # Endpoints created in the last 24h
   fly machine exec -a webhook-inspector-db <machine-id> \
     "bash -c 'PGPASSWORD=\$OPERATOR_PASSWORD psql -h localhost -U postgres -d webhook_inspector -c \"SELECT COUNT(*) FROM endpoints WHERE created_at > NOW() - INTERVAL ''24 hours'';\"'"
   ```

3. **Good outcome** (>500 unique visits, >50 stars): queue a Product
   Hunt post for J+2.
4. **Bad outcome** (<200 visits, <10 stars): not a verdict on the
   product. Product is live, you keep improving. Try again in 2-3
   months with a different angle (vertical: "Stripe webhook debugging
   stack" / "Self-hosted webhook tools comparison").

### J+2: Tech blog post (optional)

If energy permits:

**Title**: _"Building hooktrace.io: webhook observability with OpenTelemetry"_

Outline:
1. The problem (debugging webhooks across 3 services manually)
2. The architecture (FastAPI + arq + Fly + R2 + Honeycomb)
3. Decisions I'd defend (Clean Architecture / DDD ports for Redis/repos)
4. Decisions I'd reconsider (self-managed PG = backup pain)
5. The GCP→Fly migration (link to public plan)
6. What HN commentary taught me (fresh, real, day after)

Cross-post: dev.to, Hashnode, Medium. Link back in the HN thread:
_"Post-mortem of this Show HN: <link>"_.

### Long-tail feedback tracking

Keep `/tmp/launch-feedback.md`:
- 5+ people requesting the same feature → roadmap V4
- 3+ people hitting the same bug → fix immediately
- One precise architectural critique → review on its own merits, may
  justify a refactor

---

## Pre-launch TODO checklist

- [ ] J-7: run the 9 smoke tests above, all pass
- [ ] J-5: seed traffic + take 2 screenshots, upload to Imgur, paste
      links into this file
- [ ] J-5: optional Kap/LICEcap GIF, upload to Imgur
- [ ] J-3: optionally re-run k6 against prod for fresh Q8 numbers — the
      checked-in numbers are real and recent but not same-day.
- [ ] J-3: re-read post body for typos, ensure title is ≤80 chars
- [ ] J-1: 5 final checks above, all green
- [ ] J-1: confirm date + 14:00 UTC slot on calendar
- [ ] Day J: post + first comment within 30s
