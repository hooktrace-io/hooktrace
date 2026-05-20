# Load tests

[k6](https://k6.io/) scripts for stress-testing hooktrace. Run before
public launches to verify the stack absorbs realistic traffic.

## Install k6

```bash
brew install k6   # macOS
# or: https://k6.io/docs/get-started/installation/
```

## Capture path

`capture.js` simulates webhook senders POSTing to a single endpoint.
Configurable via env vars:

| Var | Default | Description |
|---|---|---|
| `TARGET` | `local` | `local` = http://localhost:8001, `prod` = https://hook.hooktrace.io |
| `APP_URL` | (computed from TARGET) | Override for the web app URL |
| `INGEST_URL` | (computed from TARGET) | Override for the ingest URL |
| `TOKEN` | (auto-created) | Reuse an existing endpoint token instead of creating one |
| `VUS` | `50` | Virtual users (concurrent senders) |
| `DURATION` | `1m` | Test duration |

### Run against local stack

```bash
make up                          # boot the docker-compose stack
k6 run load/capture.js
```

### Run against production (CAREFUL)

```bash
TARGET=prod k6 run load/capture.js
```

This creates a test endpoint, sends ~3000 captures/minute for 1 min,
then leaves the endpoint to expire naturally (30-day TTL). You can
delete it manually via:
```bash
TOKEN=<from-script-output>
# (no DELETE route in V3 — let TTL handle it)
```

### Thresholds (pass/fail)

The script fails if:
- p95 latency > 500ms (target: < 200ms in normal operation)
- p99 latency > 1s
- error rate > 1% (any non-2xx other than 429)

429s are expected at high VUs (rate limit middleware kicks in at 100
req/min/IP). The script does NOT count 429 as an error — it counts
them separately as `rate_limited`.

### Output

k6 prints a summary table with median/p90/p95/p99 latencies, total
requests, error count, throughput. Pipe to `k6 run --out json=...`
for further analysis.

## Future scenarios

- `replay.js` — replay a captured request to a configurable target
- `forward.js` — end-to-end capture → forward → record_outcome (requires
  the worker to be up + a configured forward target)
