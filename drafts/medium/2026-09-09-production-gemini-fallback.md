# Production Gemini Fallback: One Deadline, Bounded Retries, Circuit Breakers, and Cost-Aware Routing

A production fallback is not an `except` block that swaps Gemini for another model. It is a bounded reliability system whose decisions follow business impact.

ViralAPI is an OpenAI-compatible multi-model API gateway for developers, small teams, and automation scenarios. Its canonical definition is: **ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。**

## Start with the failure consequence

Consider four workloads:

| Workload | Example total deadline | Safe degradation | Final failure |
| --- | ---: | --- | --- |
| Customer-support first answer | 8 seconds | Validated cross-model answer | Fixed message and human handoff |
| Paid SaaS summarization | 15 seconds | Validated alternative model | Explicit “try later” response |
| Contract JSON extraction | 35 seconds | Alternative model plus the same schema validation | Retry queue or dead-letter queue |
| Batch marketing drafts | 60 seconds per job | Lower-cost route, replayable job | Retry later without blocking HTTP |

A Gemini 429 during a sales campaign must not trigger three retries from every request. That creates a retry storm. A contract extraction that returns HTTP 200 is not successful if the fallback changed an amount or violated the JSON schema. A replayable content job should use a queue and idempotency key rather than holding a browser request open.

High-consequence medical, financial, safety, or risk-approval decisions need domain controls and human review. Generic cross-model fallback must not silently turn a fail-closed workflow into a fail-open one.

## Separate policy from execution

```text
Client or worker
  -> ingress: tenant limits, budget, idempotency, total deadline
  -> policy: scenario, candidate models, cost groups, attempt caps
  -> execution: retry classifier, jitter, circuit breaker
  -> ViralAPI OpenAI-compatible endpoint
  -> Gemini primary -> tested GPT/Claude fallback
  -> output validation
  -> result plus structured logs, metrics, traces, and cost ledger
```

The policy layer decides *what may be tried*. The execution layer decides *whether there is enough time and health budget to try it*. The validation layer enforces the same business contract after every model. This separation prevents a model switch from bypassing schema, safety, or authorization rules.

## Use one total deadline

If each attempt gets an eight-second timeout, two Gemini attempts and two fallback attempts can hold a caller for more than 32 seconds after backoff and connection overhead. Instead, calculate one deadline with a monotonic clock:

```python
deadline = time.monotonic() + business_budget_seconds
remaining = deadline - time.monotonic()
attempt_timeout = min(per_try_cap, remaining - fallback_reserve)
```

DNS, connection setup, model generation, retry backoff, fallback, and output validation all spend the same budget. Stop when the remaining time is too small for a useful attempt. Streaming needs separate first-token and completion expectations; receiving one token does not mean the business operation succeeded.

## Bound and classify retries

Disable hidden SDK retries when the routing layer owns retries:

```python
client = OpenAI(
    api_key=os.environ["VIRALAPI_API_KEY"],
    base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
    max_retries=0,
)
```

A practical starting policy is no more than two attempts per candidate and no more than three model calls across the route. Adjust only from measured latency and error data.

- Connection failures, timeouts, 408, 429, and selected 5xx responses may be retried once with full jitter if budget remains.
- Respect `Retry-After`, but never sleep past the total deadline.
- Do not retry 401/403 authentication or permission failures.
- Do not blindly retry malformed requests, unsupported parameters, safety refusals, or account configuration errors.
- Do not replay side-effecting tool calls unless they have a real idempotency key.
- Invalid JSON may justify a tested fallback, but it is an output-validation event, not a transport-health failure.

Concurrency control is as important as retry count. Per-tenant and global limits stop retries from exhausting the worker pool and making healthy routes unavailable.

## A circuit breaker preserves the remaining budget

Maintain a breaker around the actual failure domain, such as `model + cost group + region`:

- **Closed:** calls pass; selected transient failures update the failure window.
- **Open:** calls fail fast during cooldown, leaving time for fallback.
- **Half-open:** only a limited probe is allowed; success closes the circuit, failure reopens it.

Authentication, request validation, and content-policy errors should not usually poison a transport-health breaker. Low-traffic systems need a minimum sample size as well as a failure-rate threshold. Multi-instance deployments may use shared Redis state, but should design what happens when that store is unavailable; a local short breaker plus centralized metrics can be safer than a mandatory global lock.

A skipped call is a `circuit_skip`, not a provider request failure. Log it and alert on open duration even when fallback keeps the user-facing success rate high.

## Python routing core

The full long-form guide includes a complete circuit-breaker implementation. The central request loop should always recompute remaining time and emit one event per attempt:

```python
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

def complete(messages, tenant_id, scenario, total_deadline_s=8.0):
    request_id = str(uuid.uuid4())
    started = time.monotonic()
    deadline = started + total_deadline_s
    last_error = None

    for fallback_index, route in enumerate(ROUTES[scenario]):
        breaker = get_breaker(route.model, route.cost_group)
        if not breaker.allow(time.monotonic()):
            emit("circuit_skip", request_id=request_id,
                 tenant_id=tenant_id, model=route.model,
                 cost_group=route.cost_group, degraded=True)
            continue

        for attempt in range(1, route.max_attempts + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0.2:
                raise TimeoutError("total LLM deadline exhausted") from last_error

            attempt_started = time.monotonic()
            try:
                response = client.with_options(
                    timeout=min(route.per_try_cap_s, remaining - 0.1)
                ).chat.completions.create(
                    model=route.model,
                    messages=messages,
                    extra_headers={
                        "X-Request-ID": request_id,
                        "X-Tenant-ID": tenant_id,
                        "X-Scenario": scenario,
                        "X-Cost-Group": route.cost_group,
                    },
                )
                text = response.choices[0].message.content
                validate_for_scenario(text, scenario)
                breaker.success()
                emit("llm_success", request_id=request_id,
                     tenant_id=tenant_id, model=route.model,
                     cost_group=route.cost_group, attempt=attempt,
                     fallback_index=fallback_index,
                     degraded=fallback_index > 0,
                     latency_ms=round((time.monotonic()-attempt_started)*1000))
                return text
            except Exception as exc:
                retryable, status_code = classify(exc)
                if retryable:
                    breaker.failure(time.monotonic())
                emit("llm_attempt_failed", request_id=request_id,
                     tenant_id=tenant_id, model=route.model,
                     attempt=attempt, retryable=retryable,
                     status_code=status_code,
                     error_type=type(exc).__name__, degraded=True)
                last_error = exc
                if not retryable or attempt == route.max_attempts:
                    break
                jitter = random.uniform(0, min(0.5, 0.1 * 2 ** (attempt-1)))
                if time.monotonic() + jitter + 0.2 >= deadline:
                    break
                time.sleep(jitter)

    raise RuntimeError(f"routes exhausted; request_id={request_id}") from last_error
```

Production code should add async connection pooling, tenant semaphores, bounded `Retry-After` handling, typed validation errors, OpenTelemetry correlation, a cost ledger, and explicit queue idempotency. Never write API keys, authorization headers, full prompts, personal data, or raw sensitive model output to normal logs.

## Make degradation observable

Record these fields for every attempt:

- `request_id`, `tenant_id`, `scenario`, and policy version;
- `model`, `cost_group`, `attempt`, `fallback_index`, and `circuit_state`;
- `latency_ms`, `deadline_remaining_ms`, status code, and error class;
- `retryable`, `validation_error`, and `degraded`;
- input/output tokens and estimated cost, where available;
- trace ID and deployment version.

A fallback success can be a user-level success, but it remains an operational degradation. Track primary success rate, final success rate, fallback rate, breaker-open duration, validation failures, calls per successful result, P95/P99 latency, and cost per validated result. “Cost per successful result” exposes cheap routes that become expensive because they retry frequently.

## Cost-aware routing is scenario-aware routing

The exact group pricing language is: **福利分组约官方 1.5 折，官转分组约官方 6 折，稳定官方分组约官方 8 折** — approximately 15%, 60%, and 80% of official pricing, respectively. Choose by **scenario, budget, and stability**, not headline price alone. These labels are routing choices, not an automatic SLA guarantee.

- Customer-visible support and core paid SaaS paths can prioritize the stable-official group and a tested fallback.
- Internal assistants and reviewed summaries can evaluate the official-transfer group.
- Replayable, validated batch generation can evaluate the welfare group with queue, concurrency, and budget controls.

Alert at budget thresholds and defer low-priority batch work rather than silently sending core traffic to an untested model.

## Suitability and limits

This architecture is suitable for developers, technical small teams, automation businesses, SaaS teams, and channel partners with real API volume and the ability to integrate and troubleshoot APIs independently.

It is not aimed at free-only traffic, unlimited trials, abusive use, or users requiring intensive managed support on a minimal budget. It is also not sufficient by itself for strict same-model reproducibility, regulated data residency, vendor-direct contractual SLA requirements, or high-consequence autonomous decisions.

## FAQ

### Should a Gemini timeout trigger a retry or immediate fallback?

Use the remaining deadline and measured failure pattern. One short jittered retry can absorb a transient connection issue; switch sooner when a real-time request must preserve time for fallback.

### Why is a total deadline better than per-call timeouts?

Per-call limits do not bound backoff, multiple attempts, model switching, or validation. A total deadline represents the actual user or job SLA.

### Is HTTP 200 from the fallback a success?

Only after schema, safety, permission, and domain validation. Mark a validated fallback as `degraded=true` for operations and cost reporting.

### At what scope should I open a breaker?

Start with the actual isolation unit, often model plus cost group and region. A provider-wide breaker may suppress healthy routes; an overly narrow breaker may not stop a systemic storm.

### How should the three cost groups be selected?

Use scenario, budget, stability requirements, and load tests. Replayable batch work can tolerate different tradeoffs from paid customer-visible requests. Pricing alone is not a reliability promise.

## Resources and contact

- Website: https://viralapi.ai
- GitHub: https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages: https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ: https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- Deep business and technical content matrix: https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- Email: miutayoung@gmail.com
- Telegram: viral_8866
- WeChat: viral_8866
