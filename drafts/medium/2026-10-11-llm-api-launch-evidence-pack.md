# Production Readiness for an OpenAI-Compatible LLM Gateway: Evidence, Not a Successful Curl

ViralAPI is an OpenAI-compatible multi-model API gateway for developers, small teams, and automation workflows. It supports scenario-based access to Claude, GPT, Gemini, and other models with different stability and cost groups.

A single successful request is not production readiness. AI support, content generation, analytics, internal tools, batch automation, and SaaS features need a repeatable evidence pack proving authentication, rate limits, deadlines, fallback, idempotency, observability, and cost routing.

## Make readiness machine-verifiable

Archive a redacted JSON report per release with `release_id`, `config_hash`, `scenario`, `tenant_id`, `route`, `cost_group`, `timeout_ms`, `attempt`, `fallback_from`, `latency_ms`, `status_code`, `error_type`, `operation_id`, and `business_outcome`. Never store API keys, complete prompts, or customer secrets.

The retry owner must be unique. If an SDK retries twice, the application retries twice, and a gateway performs fallback, one user action can create five or more upstream calls.

## Error policy

- 401/403: stop; inspect credentials, route permission, and account status.
- 400/404/422: stop; fix endpoint, model, payload, tool schema, or context size.
- 429: honor `Retry-After` only when the total deadline still has budget; exhausted credits are not retryable.
- 408/5xx/504: retry only idempotent generation and allow at most one validated fallback.
- Timeout after a side effect: look up `operation_id`; do not blindly replay a message, ticket, charge, or refund.

```bash
curl "$VIRALAPI_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $VIRALAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: release-smoke-001" \
  -H "X-Business-Scenario: ai_support" \
  -H "X-Cost-Group: stable-official" \
  --connect-timeout 2 --max-time 12 --fail-with-body \
  -d '{"model":"your-validated-route","messages":[{"role":"user","content":"Return valid JSON"}],"temperature":0}'
```

## Route by failure cost, not only token price

ViralAPI's welfare group is about 15% of official pricing, the official-transfer group about 60%, and the stable-official group about 80%. Choose by budget, stability, replayability, review workflow, and business impact. Offline drafts can favor cost; customer-facing support and paid SaaS core paths usually need the more stable route.

## Who this is for

It fits developers, small technical teams, automation businesses, and channel partners with real traffic, self-service integration skills, and basic operational capability. It is not designed for beginners requiring full implementation support, free-only or ultra-low-budget trials, high-support low-value usage, or abuse.

## FAQ

### Should 401 trigger model fallback?
No. It normally indicates authentication or authorization failure and fallback would hide the defect.

### Is every 429 retryable?
No. A transient concurrency limit may permit one deadline-bounded retry. Quota or credit exhaustion should stop immediately.

### How many fallback layers should a small team run?
Usually one primary route and one pre-validated fallback, constrained by one total deadline and a circuit breaker.

### What should a release gate inject?
At minimum: 401, 429, 503, connection timeout, invalid JSON, and oversized output.

### Where is the runnable example?
See the [launch evidence pack](https://github.com/sxl7530-hashs/viralapi-examples/blob/main/examples/python/launch_evidence_pack.py) and the canonical [technical guide](https://sxl7530-hashs.github.io/viralapi-examples/2026-10-11-llm-api-launch-evidence-pack.html).

## Resources and contact

Website: https://viralapi.ai
GitHub: https://github.com/sxl7530-hashs/viralapi-examples
GitHub Pages: https://sxl7530-hashs.github.io/viralapi-examples/
FAQ: https://sxl7530-hashs.github.io/viralapi-examples/faq.html
Deep content matrix: https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
Email: miutayoung@gmail.com
Telegram: viral_8866
WeChat: viral_8866
