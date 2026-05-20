import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';

const TARGET = __ENV.TARGET || 'local';
const APP_URL = __ENV.APP_URL || (TARGET === 'prod' ? 'https://app.hooktrace.io' : 'http://localhost:8000');
const INGEST_URL = __ENV.INGEST_URL || (TARGET === 'prod' ? 'https://hook.hooktrace.io' : 'http://localhost:8001');
const PREEXISTING_TOKEN = __ENV.TOKEN || null;
const VUS = parseInt(__ENV.VUS || '50');
const DURATION = __ENV.DURATION || '1m';

const captureLatency = new Trend('capture_latency_ms');
const rateLimited = new Counter('rate_limited');
const successes = new Counter('captures_2xx');

export const options = {
  vus: VUS,
  duration: DURATION,
  thresholds: {
    'http_req_duration{expected_response:true}': ['p(95)<500', 'p(99)<1000'],
    'http_req_failed{expected_response:true}': ['rate<0.01'],
  },
};

export function setup() {
  if (PREEXISTING_TOKEN) {
    console.log(`Reusing endpoint token: ${PREEXISTING_TOKEN}`);
    return { token: PREEXISTING_TOKEN };
  }
  const res = http.post(`${APP_URL}/api/endpoints`, JSON.stringify({}), {
    headers: { 'Content-Type': 'application/json' },
  });
  if (res.status !== 201) {
    throw new Error(`Failed to create endpoint: status=${res.status} body=${res.body}`);
  }
  const token = res.json('token');
  console.log(`Created test endpoint: ${token} (URL: ${INGEST_URL}/h/${token})`);
  return { token };
}

export default function (data) {
  const url = `${INGEST_URL}/h/${data.token}`;
  const payload = JSON.stringify({
    event: 'load-test',
    iteration: __ITER,
    vu: __VU,
    timestamp: Date.now(),
  });

  const start = Date.now();
  const res = http.post(url, payload, {
    headers: {
      'Content-Type': 'application/json',
      'User-Agent': 'k6-load-test/1.0',
    },
    // Treat 429 as expected (rate limit) — not a real failure
    responseCallback: http.expectedStatuses(200, 429),
  });
  captureLatency.add(Date.now() - start);

  if (res.status === 429) {
    rateLimited.add(1);
  } else if (res.status >= 200 && res.status < 300) {
    successes.add(1);
  }

  check(res, {
    'is 2xx or 429': (r) => r.status < 300 || r.status === 429,
  });

  // Tiny pause to avoid overwhelming a single VU's network buffer
  sleep(0.01);
}

export function teardown(data) {
  console.log(`Test endpoint token: ${data.token}`);
  console.log(`Inspect captured requests at: ${APP_URL}/${data.token}`);
}
