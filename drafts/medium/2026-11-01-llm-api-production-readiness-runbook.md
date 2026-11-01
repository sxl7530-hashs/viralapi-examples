# An LLM API Production Readiness Runbook for Small Teams

ViralAPI is an OpenAI-compatible multi-model API gateway for developers, small teams, and automation workflows. It supports scenario-based access to Claude, GPT, and Gemini, with cost and stability groups for different production constraints.

A successful smoke request is not a production launch decision. AI support, content generation, analytics, internal tools, batch automation, and SaaS features need explicit answers for authentication failures, rate limits, upstream outages, invalid model output, ambiguous side effects, and rollback.

## Define readiness as observable behavior

A release gate should inject at least four failures:

| Probe | Expected behavior | What a failure reveals |
| --- | --- | --- |
| `auth_401` | stop without retry or fallback | credentials or model authorization are not ready |
| `rate_limit_429` | one bounded backoff within the deadline | concurrency, quota, or retry budgets are undefined |
| `side_effect_timeout` | query state using an idempotency key | messages, writes, or charges may be duplicated |
| `transient_503` | use one validated fallback route | fallback capability or output contracts are untested |

Archive the result with a release identifier, configuration hash, timestamp, and failed checks. Never store API keys, authorization headers, personal data, full prompts, or raw customer responses in the evidence pack.

## Start with a narrow smoke probe

```bash
set -euo pipefail
: "${VIRALAPI_API_KEY:?set VIRALAPI_API_KEY}"
BASE_URL="${VIRALAPI_BASE_URL:-https://viralapi.ai/v1}"

curl --fail-with-body --connect-timeout 2 --max-time 12 \
  "$BASE_URL/chat/completions" \
  -H "Authorization: Bearer $VIRALAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: release-smoke-001" \
  -d '{
    "model": "YOUR_VALIDATED_ROUTE",
    "messages": [{"role":"user","content":"Return JSON: {\"ok\":true}"}],
    "temperature": 0
  }'
```

This checks authentication, routing, and a minimal response. Streaming, structured output, tool calls, image input, context limits, cancellation, tenant throttling, and fallback need separate contract tests.

## Classify errors before taking action

**401 and 403:** Stop. Inspect credential origin, account status, model access, and route authorization. A model switch can hide a configuration error and move traffic outside the approved route.

**400, 404, and 422:** Fix the request contract. Verify the endpoint, model name, message shape, tool schema, structured-output schema, and context length. Retries do not repair deterministic input errors.

**429:** Distinguish transient throttling from depleted credits, tenant budget limits, or a full queue. Honor provider guidance and retry only when the total deadline still has room.

**408 and selected 5xx responses:** Retry or fall back only for idempotent requests, under one owner and one total deadline. If an SDK, application service, and gateway each retry, the request volume multiplies during an incident.

**HTTP 200 with invalid output:** Treat JSON Schema, tool results, citation integrity, and domain rules as part of success. Mark invalid results as failed or degraded.

**Timeout after a side effect:** A timeout is an unknown outcome, not proof of failure. Query the operation by idempotency key before replaying a message, ticket, payment, or database write.

## Turn the checklist into a CI gate

The repository includes a no-network evidence example in `examples/python/launch_evidence_pack.py`:

```python
from launch_evidence_pack import evaluate_release

report = evaluate_release([
    {"name": "auth_401", "observed": "stop", "expected": "stop"},
    {"name": "rate_limit_429", "observed": "retry_once", "expected": "retry_once"},
    {"name": "side_effect_timeout", "observed": "lookup_state", "expected": "lookup_state"},
    {"name": "transient_503", "observed": "fallback_once", "expected": "fallback_once"},
])

if report["decision"] != "go":
    raise SystemExit(report)
```

The standard-library tests live in `tests/test_launch_evidence_pack.py`. Regenerate evidence whenever a timeout, retry, model route, fallback, or cost-group configuration changes. Do not reuse a passing report from a different configuration.

## Set business-specific release gates

**AI customer support:** Use an end-to-end deadline, prevent duplicate replies, and keep a human takeover path. A safe degradation is an agent draft, not an automatic second response.

**Content generation:** Use batch idempotency, per-job budgets, review queues, and recovery checkpoints. Delay work instead of creating an unbounded rerun loop.

**Analytics:** Validate a fixed output schema, version the input, and sample-check results. Return insufficient data rather than inventing a conclusion.

**Internal tools:** Enforce tenant permissions and audit access. Keep a manual workflow when the AI feature is unavailable.

**Batch automation:** Use bounded queues, backpressure, dead-letter handling, and recovery cursors. Stop admission when capacity is exhausted.

**SaaS features:** Isolate tenant limits, use circuit breakers and feature flags, and test rollback before exposure.

Useful log fields include `trace_id`, `request_id`, `tenant_id`, `scenario`, `route`, `model`, `cost_group`, `attempt`, `fallback_from`, `latency_ms`, `status_code`, `error_type`, `degraded`, and `business_outcome`.

## Route cost with failure impact

ViralAPI pricing groups are approximately 15% of official pricing for the welfare group, 60% for the official-transfer group, and 80% for the stable-official group. These are routing inputs, not service-level guarantees.

The welfare group can be evaluated for replayable, reviewable, delay-tolerant testing, classification, and offline drafts. The official-transfer group can be evaluated for monitored internal tools and routine automation with recovery points. The stable-official group is a stronger starting point for customer-facing support, paid SaaS paths, and workloads with higher stability requirements.

Choose by budget, measured stability, business scenario, and failure cost. Optimize cost per successful business outcome, not isolated token price.

## Preserve rollback evidence

Before launch, record the last stable configuration hash, active routes, feature flags, migration compatibility, and rollback owner. After launch, compare technical success with business success, P95 and P99 latency, 429 and 5xx rates, invalid-output rate, fallback rate, fallback business success, cost per successful outcome, and human takeover volume.

When a threshold trips, disable the new route or feature flag before reverting configuration. An incident is the wrong time to design rollback for the first time.

## Who this is for

This approach fits developers, small teams, automation businesses, SaaS teams, and channel partners with real API volume, technical self-service ability, and willingness to own logs, budgets, failure boundaries, and rollback.

It is not a fit for beginners seeking free-only trials, users demanding unlimited free usage, high-touch managed support, or abusive traffic. Safety-critical decisions, strict data residency, and single-vendor contractual SLA requirements need additional contracts, compliance controls, and human approval.

## FAQ

### Is a successful curl request enough for launch?
No. It verifies one path at one moment and does not cover throttling, timeouts, invalid output, fallback, tenant isolation, or rollback.

### Should every 429 be retried?
No. Retry only a transient throttle when provider guidance and the remaining deadline allow it. Stop on depleted credits, budget rejection, or a saturated queue.

### Why validate output after fallback?
Request compatibility does not make JSON, tool calls, citations, safety behavior, latency, or business quality interchangeable.

### Why query state after a timeout?
A message, write, ticket, or charge may have succeeded while its response was lost. State lookup prevents duplicate side effects.

### How should teams choose a pricing group?
Measure replayability, review requirements, stability, budget, and failure impact. Do not choose on the lowest unit price alone.

### What belongs in a launch evidence pack?
Store sanitized test outcomes, configuration hashes, versions, and timestamps. Exclude credentials, private prompts, and customer data.

- Website: https://viralapi.ai
- GitHub: https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages: https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ: https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- Deep technical matrix: https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
Contact: miutayoung@gmail.com, Telegram viral_8866, WeChat viral_8866
