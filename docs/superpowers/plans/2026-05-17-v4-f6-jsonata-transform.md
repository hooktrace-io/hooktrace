# V4 — F6 JSONata payload transform (research + execution plan)

> **Status** : **Deferred from V3 Phase 0** on 2026-05-17 after plan review found 8 non-trivial findings (4 High + 4 Med). This document captures the full feature spec, the open decisions, the library landscape, the problems to solve, and a conditional execution outline so the feature can be picked up cleanly in V4 (or earlier if customer discovery validates demand).
>
> **Re-evaluation trigger** : Phase 1 customer discovery (cf. `docs/launch/2026-05-15-launch-plan.md` section 3) should explicitly probe whether interviewees want payload reshaping. If <60% identify it as a blocker, defer further. If ≥60%, ship in V4 using this plan.

---

## 1. What F6 does — feature spec

### User story

A user configures their hooktrace endpoint to receive webhooks from Stripe (for example), and wants to forward to their internal backend. Their backend doesn't speak Stripe's payload shape — it expects something simpler. Without transformation, every such user has to either:

- Write glue code in their own backend (rejects the no-code pitch)
- Insert a Zapier/Make/n8n middleman (expensive, slow, another vendor)
- Run their own webhook receiver that adapts before re-emitting (defeats the purpose of using hooktrace)

With F6, the user pastes a JSONata expression into their endpoint config (`PATCH /api/endpoints/{token}/config`). On every captured webhook destined for forward (PR7), the body is parsed, transformed, re-serialized, and POSTed to their target.

### Concrete example

Stripe sends :
```json
{
  "id": "evt_3MQk9F2eZvKYlo2C1Wcsmzkk",
  "object": "event",
  "api_version": "2024-04-10",
  "created": 1716000000,
  "data": {
    "object": {
      "id": "ch_3MQk9F2eZvKYlo2C04Vd6Ke7",
      "amount": 4200,
      "currency": "eur",
      "metadata": { "order_id": "ord_42" }
    }
  },
  "type": "charge.succeeded"
}
```

User's backend expects :
```json
{ "orderId": "ord_42", "amountCents": 4200 }
```

User pastes :
```jsonata
{ "orderId": data.object.metadata.order_id, "amountCents": data.object.amount }
```

Done. No code, no redeploy, change anytime via PATCH.

### Where it sits in the V3+ pitch

`docs/launch/2026-05-15-launch-plan.md` lists F6 (Transform) as a Pro-tier differentiator. The pitch is "you don't need Zapier for simple reshaping". V4 promotes this to a real revenue feature.

---

## 2. Why deferred from V3

Eight problems surfaced during plan review (2026-05-17). Each individually fixable, but together they represent ~5 days of work for a feature that :
- Is not in the V3 value prop core (capture / replay / timeline / forward are)
- Adds significant attack surface (sandbox, CPU isolation, library upkeep)
- Has no customer-validated demand yet — Phase 1 interviews not done

The 5-day estimate covers (in rough order) :

| Block | Time |
|---|---|
| Library decision + benchmark on the 5 reference use cases | 0.5 day |
| ProcessPoolExecutor + timeout robust to async | 0.5 day |
| Sandbox (RLIMIT + output cap + recursion) | 1 day |
| Body content-type handling (4 formats × tests) | 1 day |
| Compile-at-PATCH-time + error UX | 0.5 day |
| Content-Type recompute logic | 0.25 day |
| DB schema + Pydantic length cap | 0.25 day |
| Public docs (JSONata primer + known library gaps) | 1 day |
| **Total** | **~5 days** |

V3 Phase 0 dropped from 10-11 weeks to ~9-10 weeks ; F6 deferral preserves rhythm without losing core pitch.

---

## 3. Open decisions to resolve before scoping

### Decision A — JSONata library choice

The Python JSONata ecosystem is significantly behind JavaScript. Three options exist :

**Option A1 : `jsonata-python` (IBM, PyPI)**
- Coverage : ~80% of the JSONata spec
- Performance : ~10× slower than the JS reference implementation (it's a port, not optimized)
- Last release : 2024
- Known gaps : sequence flattening rules, higher-order lambdas, `$match`/`$replace` regex helpers partial
- Maintenance : single corporate maintainer, low activity but not abandoned

**Option A2 : Pivot to `jmespath` (AWS, well-maintained)**
- Different syntax — `jmespath` is **not** JSONata, users have to learn a new DSL
- Coverage of the JMESPath spec is complete
- Performance : native Python, fast
- Used widely (AWS CLI, Ansible, many Python tools)
- Docs are crisp
- Trade-off : the "JSONata" pitch becomes "JMESPath" pitch — less familiar to webhook-savvy users who've seen JSONata in Node-RED / n8n / IBM tools, more familiar to AWS/devops users

**Option A3 : Subset of JSONata implemented in Python**
- Original plan estimated 500 LOC. **Realistic estimate is 2000+ LOC** for a useful subset (path navigation + filters + projections + arithmetic + sequence flattening + basic `$map`/`$reduce`)
- Maintenance burden : every "why does this expression work in JS but not here" issue lands in our backlog
- Strong recommendation : **avoid** unless A1 fails the reference use cases.

**Decision criteria** :
- Run the 5 reference expressions (below) on A1
- If A1 passes all 5 cleanly → go with A1, document the 20% known gaps publicly
- If A1 fails ≥1 → switch to A2 (rename feature to "JMESPath transform") rather than write a subset

### Decision B — Sandbox enforcement model

JSONata is Turing-complete-ish (lambdas via `function()`, `$reduce`, `$map`). Pathological expressions can consume gigabytes of RAM and seconds of CPU. Without sandbox, **one malicious or accidental user expression** can OOM the Fly Machine (256 MB) and take down forwarding for everyone.

Three sandbox approaches :

**Option B1 : ProcessPoolExecutor + RLIMIT**
- Each transform runs in a forked Python subprocess with `resource.setrlimit(RLIMIT_AS, 100*1024*1024)` (100 MB virtual memory cap) and `RLIMIT_CPU` (1s CPU time).
- IPC overhead : 10-50 ms per call (pickle of input/output).
- Kills cleanly on timeout via `Process.kill()`.
- Best isolation, highest cost.

**Option B2 : Threadpool + best-effort timeout (no hard kill)**
- Runs in `ThreadPoolExecutor` with `asyncio.wait_for(future, timeout=1.0)`.
- `asyncio.TimeoutError` raises in the caller, but the **thread keeps running** (Python doesn't support thread cancellation). Memory leak accumulates.
- Acceptable only if (a) expression length is capped tightly (prevents most pathological cases) AND (b) we have alerting on memory growth.
- Lower cost (~µs overhead per call), worse isolation.

**Option B3 : Pre-evaluation static analysis + reject dangerous expressions**
- Walk the parsed AST before evaluation, reject expressions containing `$reduce`, lambdas, or nested `$map` calls > N levels.
- Cheap, but restricts users to "safe subset" of JSONata.
- Doesn't actually require sandbox at runtime — just a parse-time filter.

**Decision criteria** :
- Phase 0 user base is small (<100 endpoints expected at launch). B1 is safe but heavyweight.
- B3 is the simplest path to ship if we accept "Pro tier feature with restricted expression syntax".
- Recommendation : **start B3** (parse-time filter), revisit to B1 if customer demand justifies the safe-but-slow path.

### Decision C — Validation timing (compile vs runtime)

Two extremes :

**Option C1 : Compile at PATCH config time, fail-fast**
- Pydantic validator on the `transform` field calls the library's `compile()`.
- Syntax error → 400 response immediately, user sees the error.
- Stored expression is guaranteed valid syntax.
- Runtime errors (eval against unexpected payload shape) still possible but logged loudly.

**Option C2 : Silent fallback to original payload on any error**
- Original plan's approach. Easy to implement.
- **User-hostile** : the user pastes an invalid expression, gets 200 OK on PATCH, never sees a transform happen, has no signal something's wrong.

**Decision** : C1 unconditionally. Silent fallback is an anti-pattern for user-configurable input.

### Decision D — Body content-type handling

Webhooks come in multiple content types :

| Sender | Content-Type |
|---|---|
| Stripe, GitHub, Slack, Discord, OpenAI, Anthropic | `application/json` |
| Shopify | mix (newer = JSON, legacy = form-encoded) |
| Twilio | `application/x-www-form-urlencoded` |
| Mailgun | `multipart/form-data` (with attachments) |
| Legacy SaaS | `application/xml` |
| Custom | anything |

JSONata only operates on JSON-like trees. Options for non-JSON bodies :

**Option D1 : Skip transform, forward original body**
- Log warning + increment `transform_skipped_total{reason="non_json_body"}`
- User sees in the DLQ UI that forwarding worked but transform was skipped

**Option D2 : Best-effort coercion**
- form-encoded → parse to dict of strings → transform → re-serialize as JSON
- multipart → too complex, skip
- XML → too complex, skip
- Risk : users don't expect "form" to silently become JSON downstream

**Decision** : D1. Simpler contract, no surprise coercion. Document loudly in the Pro tier description : "transforms apply only to JSON payloads".

### Decision E — Output size cap

`$map([1..1000], function($i){{"item": $i, "padding": "x" * 1000}})` produces ~1 MB output from a trivial input. Without a cap, the forward POST balloons, target rejects with 413, retry cascades.

**Decision** : `MAX_FORWARD_BODY_BYTES = 1 MB` post-transform. If output exceeds, fail the forward with `final_error="transform output > 1 MB"`. Same constant as `MAX_REPLAY_BODY_BYTES` in PR4.

### Decision F — Expression length cap

User can PATCH a 1 MB expression. Compilation cost scales with length. Plus DB row bloat.

**Decision** : `Field(max_length=1024)` on Pydantic + `CHECK (LENGTH(transform_expression) <= 1024)` in migration. Real-world JSONata expressions are <200 chars.

---

## 4. Detailed problem analysis (from V3 review)

### Problem 1 — Python JSONata ecosystem fragility

Resolved by Decision A. See library landscape above. Action : bench A1 on the 5 reference cases before committing.

### Problem 2 — `signal.SIGALRM` does not work in async/threaded Python

The original V3 plan used `signal.SIGALRM` for timeout. **It doesn't work** :
- `signal.SIGALRM` only fires in the main thread of the main process on POSIX.
- async event loop tasks can be interrupted mid-await with corrupted state.
- Offloading to `ThreadPoolExecutor` means SIGALRM is never received in the worker thread.

Resolved by Decision B (sandbox model). Implementation pattern (if B1 chosen) :

```python
from concurrent.futures import ProcessPoolExecutor
import asyncio

_POOL = ProcessPoolExecutor(max_workers=2)  # reused across forwards


async def apply_with_timeout(expr: str, payload: dict, timeout: float = 1.0) -> dict:
    loop = asyncio.get_event_loop()
    future = loop.run_in_executor(_POOL, _eval_in_subprocess, expr, payload)
    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        # Subprocess is killed by the pool on timeout
        raise TransformerTimeoutError(f"transform exceeded {timeout}s")


def _eval_in_subprocess(expr: str, payload: dict) -> dict:
    """Runs in a forked worker process. RLIMIT applies here."""
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (100 * 1024 * 1024, 100 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (1, 1))
    import jsonata  # or jmespath, per Decision A
    return jsonata.compile(expr).evaluate(payload)
```

### Problem 3 — Turing-complete sandbox concerns

Pathological expressions enumerated :
- `$reduce([1..1000000], function($a,$v){$a * $v}, 1)` — factorial of 1M, gigabytes RAM
- `$reduce(input, function($a,$b){$a&$a}, "x")` — string doubling, 1 GB at depth 30
- Deeply nested lambdas — stack exhaustion

All mitigated by Decision B + Decision E (output cap) + Decision F (expression length cap).

### Problem 4 — Non-JSON body handling

Resolved by Decision D (skip transform for non-JSON, forward original).

### Problem 5 — Output size balloon

Resolved by Decision E.

### Problem 6 — Validation timing UX

Resolved by Decision C.

### Problem 7 — Content-Type recomputation

When transform is applied, the outbound body is JSON. The Content-Type header must be `application/json` regardless of what the original capture had. Implementation : in `execute_forward.py` (PR7's use case extended for V4), after the transform :

```python
if endpoint.transform_expression and transform_applied_successfully:
    body = json.dumps(transformed_payload).encode()
    outbound_headers["Content-Type"] = "application/json"
```

### Problem 8 — Expression length cap

Resolved by Decision F.

---

## 5. Conditional execution plan (when V4 starts)

### Step 0 — Validate decisions before coding

- [ ] Run the 5 reference JSONata expressions through `jsonata-python` (option A1). Document which pass/fail.
- [ ] Pick A1 or A2 based on results. If A1, write a public doc page listing the 20% known gaps.
- [ ] Confirm sandbox approach (B1/B2/B3) via micro-benchmark of expected forward volume × per-transform overhead.
- [ ] Lock decisions C, D, E, F as documented above unless customer feedback requires otherwise.

### Step 1 — Migration + entity

```python
# migrations/versions/00XX_<rev>_transform.py
def upgrade() -> None:
    op.add_column(
        "endpoints",
        sa.Column("transform_expression", sa.String(1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("endpoints", "transform_expression")
```

`endpoints` entity gains `transform_expression: str | None` (plain text — not a secret, no encryption).

### Step 2 — `Transformer` service

```python
# src/webhook_inspector/domain/services/transformer.py
class TransformerError(Exception): ...
class TransformerTimeoutError(TransformerError): ...
class TransformerSyntaxError(TransformerError): ...
class TransformerOutputTooLargeError(TransformerError): ...


class Transformer:
    MAX_OUTPUT_BYTES = 1 * 1024 * 1024  # 1 MB

    def __init__(self, expression: str, timeout_seconds: float = 1.0) -> None:
        self.expression = expression
        self.timeout = timeout_seconds
        # Compile at construction. Raises TransformerSyntaxError on invalid input.
        self._compiled = self._compile(expression)

    @staticmethod
    def _compile(expression: str):
        try:
            import jsonata  # or jmespath
            return jsonata.compile(expression)
        except Exception as e:
            raise TransformerSyntaxError(str(e)) from e

    async def apply(self, payload: dict) -> dict:
        """Run the transform. Raises on timeout / output too large.
        Never silently falls back — caller decides what to do on error.
        """
        # See Problem 2 implementation above
        result = await apply_with_timeout(self.expression, payload, self.timeout)
        serialized = json.dumps(result).encode()
        if len(serialized) > self.MAX_OUTPUT_BYTES:
            raise TransformerOutputTooLargeError(
                f"output {len(serialized)} > {self.MAX_OUTPUT_BYTES}"
            )
        return result
```

### Step 3 — TDD the 5 reference cases + sandbox

```python
# tests/unit/domain/services/test_transformer.py
def test_simple_path_access():
    t = Transformer("payment_intent.id")
    out = asyncio.run(t.apply({"payment_intent": {"id": "pi_123"}}))
    assert out == "pi_123"


def test_rename_and_nest():
    t = Transformer('{"paymentId": payment_intent.id, "amount": payment_intent.amount}')
    out = asyncio.run(t.apply({"payment_intent": {"id": "pi_123", "amount": 4200}}))
    assert out == {"paymentId": "pi_123", "amount": 4200}


def test_filter():
    t = Transformer("items[type='charge']")
    out = asyncio.run(t.apply({"items": [
        {"type": "charge", "id": 1}, {"type": "refund", "id": 2},
    ]}))
    assert out == {"type": "charge", "id": 1}


def test_arithmetic():
    t = Transformer('{"amount_eur": payment_intent.amount / 100}')
    out = asyncio.run(t.apply({"payment_intent": {"amount": 4200}}))
    assert out == {"amount_eur": 42}


def test_nested_array_transformation():
    t = Transformer('items.{ "id": id, "qty": quantity }')
    out = asyncio.run(t.apply({"items": [
        {"id": "a", "quantity": 1}, {"id": "b", "quantity": 2},
    ]}))
    assert out == [{"id": "a", "qty": 1}, {"id": "b", "qty": 2}]


def test_syntax_error_raises_at_compile():
    with pytest.raises(TransformerSyntaxError):
        Transformer("this is not valid")


def test_timeout_kills_pathological_expression():
    # $reduce factorial-ish, intentionally slow
    t = Transformer(
        "$reduce([1..100000], function($a,$v){$a * $v}, 1)",
        timeout_seconds=0.1,
    )
    with pytest.raises(TransformerTimeoutError):
        asyncio.run(t.apply({}))


def test_output_too_large_raises():
    t = Transformer('$map([1..1000], function($i){{"k": $i, "padding": "x" & "x" & "x"}})')
    with pytest.raises(TransformerOutputTooLargeError):
        asyncio.run(t.apply({}))
```

### Step 4 — Wire into `execute_forward`

Extend `application/use_cases/execute_forward.py` (PR7) :

```python
# Inside execute(), AFTER headers fusion and BEFORE the POST :
if endpoint.transform_expression:
    content_type = outbound_headers.get("Content-Type", "").lower()
    if "application/json" in content_type:
        try:
            parsed = json.loads(body)
            transformer = Transformer(endpoint.transform_expression)
            transformed = await transformer.apply(parsed)
            body = json.dumps(transformed).encode()
            outbound_headers["Content-Type"] = "application/json"
            self.metrics.transform_applied()
        except TransformerError as e:
            # Log + record on the forward row, but DO NOT silently fall back.
            # Mark this attempt dead immediately — user needs to know.
            await self.forward_repo.record_outcome(
                forward_id, next_status="dead",
                final_status_code=None,
                final_error=f"TransformError: {type(e).__name__}: {e}",
                next_attempt_at=None, now=datetime.now(UTC),
            )
            self.metrics.transform_error(reason=type(e).__name__)
            return
        except json.JSONDecodeError:
            logger.warning("transform_skipped_invalid_json",
                           extra={"forward_id": str(forward_id)})
            self.metrics.transform_skipped(reason="invalid_json")
            # fall through, forward original body
    else:
        logger.info("transform_skipped_non_json_body",
                    extra={"content_type": content_type})
        self.metrics.transform_skipped(reason="non_json_body")
        # fall through, forward original body
```

### Step 5 — API extension `PATCH /api/endpoints/{token}/config`

```python
class EndpointConfigPatch(BaseModel):
    signature: SignatureConfig | None = None
    forward: ForwardConfig | None = None
    transform: str | None = Field(default=None, max_length=1024)  # NEW V4

    @field_validator("transform")
    @classmethod
    def validate_transform_syntax(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            Transformer(v)  # compile-only, raises on syntax error
        except TransformerSyntaxError as e:
            raise ValueError(f"invalid transform expression: {e}")
        return v
```

### Step 6 — Public documentation

Create `docs/integrations/transforming-payloads.md` (V4) with :
- JSONata syntax primer (5 examples mirroring tests)
- Known gaps if library A1 chosen
- Examples for the 9 supported senders (Stripe, GitHub, Shopify, ...)
- Errors users may see + how to debug

### DoD V4 F6

- [ ] Decision A locked + documented (library + known gaps if any)
- [ ] Decision B locked + sandbox tests pass
- [ ] All 5 reference expressions tested green
- [ ] Syntax error at PATCH → 400 with library's error message
- [ ] Timeout / output cap / non-JSON body all tested
- [ ] Content-Type rewritten to `application/json` post-transform
- [ ] Expression length cap (`max_length=1024`) enforced at Pydantic + DB
- [ ] Metrics `transform_applied_total`, `transform_skipped_total{reason}`, `transform_error_total{reason}` wired (PR8 DLQ UI surfaces these)
- [ ] `docs/integrations/transforming-payloads.md` shipped
- [ ] Commit : `feat(transform): JSONata payload transformation on forward (V4)`

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| Library A1 has more gaps than expected | Pivot to A2 (JMESPath) ; accept syntax change cost in marketing |
| ProcessPoolExecutor IPC overhead measurable at scale | Move to B3 (parse-time filter, no sandbox) |
| Users want non-JSON transforms (form-encoded) | Decision D revisit — possibly add explicit coercion mode opt-in |
| Customer discovery shows F6 is low priority | **Don't ship**, leave deferred. Save the 5 days for a feature that moves needles. |

---

## 7. References

- `docs/specs/2026-05-15-v3-observability-runtime-design.md` §F6 — original feature description
- `docs/launch/2026-05-15-launch-plan.md` — F6 positioning as Pro-tier differentiator
- `docs/superpowers/plans/2026-05-15-v3-phase0-prs.md` — sibling Phase 0 plan (V3) with F6 removed
- JSONata reference : https://docs.jsonata.org/
- `jsonata-python` : https://pypi.org/project/jsonata-python/
- JMESPath spec (alternative) : https://jmespath.org/
