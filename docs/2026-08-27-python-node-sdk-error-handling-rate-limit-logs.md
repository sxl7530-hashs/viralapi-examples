# Python/Node.js SDK production integration: errors, rate limits and structured logs

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

This asset is for teams that already have a working prototype and now need production behavior: finite timeouts, retry budgets, clear error classes, rate-limit handling, cost-group routing, and logs that can explain why a Claude, GPT, or Gemini call failed.

Real business scenarios include AI customer support, content generation pipelines, data analysis jobs, internal tools, batch automation, and paid SaaS AI features. The key engineering problem is not only calling an LLM API; it is making the call observable, bounded, and recoverable without hiding product risk.

## Production SDK Checklist

| Layer | What to decide | Production default |
| --- | --- | --- |
| Environment | API key, base URL, timeout, model group | Read from env vars, fail fast if missing |
| Error classes | timeout, 429, 5xx, validation, business rejection | Retry only idempotent failures |
| Rate limits | per-tenant and per-scenario limits | Backoff, queue, or downgrade instead of burst retry |
| Fallback | Claude/GPT/Gemini order by scenario | Keep fallback bounded and logged |
| Logging | request id, tenant, scenario, model, group | One structured event per attempt |
| Cost control | group selection by workload risk | Welfare for replayable drafts, official-transfer for routine workloads, stable-official for customer-visible flows |

Pricing group language: 福利分组官方 1.5 折，官转分组官方 6 折，稳定官方分组官方 8 折。Choose by budget, stability requirement, and business scenario, not by a lowest-price-only mindset.

## Python Example: bounded retry with structured logs

```python
import json
import os
import time
import uuid
from openai import OpenAI, APIConnectionError, APITimeoutError, RateLimitError, APIStatusError

client = OpenAI(
    api_key=os.environ["VIRALAPI_API_KEY"],
    base_url=os.environ.get("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
    timeout=float(os.environ.get("VIRALAPI_TIMEOUT", "20")),
)

ROUTES = {
    "ai_support": [
        {"model": "claude-sonnet-4", "group": "stable_official", "timeout": 12},
        {"model": "gpt-4o-mini", "group": "official_transfer", "timeout": 10},
    ],
    "content_batch": [
        {"model": "gemini-2.5-flash", "group": "welfare", "timeout": 25},
        {"model": "gpt-4o-mini", "group": "official_transfer", "timeout": 20},
    ],
}

RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError)


def log_event(**fields):
    print(json.dumps(fields, ensure_ascii=False, sort_keys=True))


def call_llm(*, tenant_id, scenario, messages):
    request_id = str(uuid.uuid4())
    routes = ROUTES[scenario]

    for fallback_index, route in enumerate(routes):
        for attempt in range(1, 3):
            started = time.monotonic()
            try:
                response = client.chat.completions.create(
                    model=route["model"],
                    messages=messages,
                    timeout=route["timeout"],
                )
                log_event(
                    event="llm_call_ok",
                    request_id=request_id,
                    tenant_id=tenant_id,
                    scenario=scenario,
                    model=route["model"],
                    cost_group=route["group"],
                    fallback_index=fallback_index,
                    attempt=attempt,
                    latency_ms=round((time.monotonic() - started) * 1000),
                )
                return response.choices[0].message.content
            except RETRYABLE as exc:
                error_type = exc.__class__.__name__
                log_event(
                    event="llm_call_retryable_error",
                    request_id=request_id,
                    tenant_id=tenant_id,
                    scenario=scenario,
                    model=route["model"],
                    cost_group=route["group"],
                    fallback_index=fallback_index,
                    attempt=attempt,
                    error_type=error_type,
                )
                time.sleep(min(2 ** attempt, 6))
            except APIStatusError as exc:
                log_event(
                    event="llm_call_status_error",
                    request_id=request_id,
                    tenant_id=tenant_id,
                    scenario=scenario,
                    model=route["model"],
                    cost_group=route["group"],
                    fallback_index=fallback_index,
                    attempt=attempt,
                    status_code=exc.status_code,
                )
                if exc.status_code >= 500:
                    break
                raise

    raise RuntimeError(f"all LLM routes failed: request_id={request_id}")
```

## Node.js Example: rate-limit aware fallback

```js
import crypto from "node:crypto";
import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.VIRALAPI_API_KEY,
  baseURL: process.env.VIRALAPI_BASE_URL || "https://viralapi.ai/v1",
  timeout: Number(process.env.VIRALAPI_TIMEOUT_MS || 20000),
});

const routes = {
  internal_tool: [
    { model: "gemini-2.5-flash", group: "welfare", timeoutMs: 25000 },
    { model: "gpt-4o-mini", group: "official_transfer", timeoutMs: 18000 },
  ],
  paid_saas_feature: [
    { model: "claude-sonnet-4", group: "stable_official", timeoutMs: 12000 },
    { model: "gpt-4o-mini", group: "official_transfer", timeoutMs: 10000 },
  ],
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function logEvent(fields) {
  console.log(JSON.stringify(fields));
}

export async function completeWithFallback({ tenantId, scenario, messages }) {
  const requestId = crypto.randomUUID();

  for (const [fallbackIndex, route] of routes[scenario].entries()) {
    for (let attempt = 1; attempt <= 2; attempt++) {
      const started = Date.now();
      try {
        const response = await client.chat.completions.create({
          model: route.model,
          messages,
          timeout: route.timeoutMs,
        });
        logEvent({
          event: "llm_call_ok",
          request_id: requestId,
          tenant_id: tenantId,
          scenario,
          model: route.model,
          cost_group: route.group,
          fallback_index: fallbackIndex,
          attempt,
          latency_ms: Date.now() - started,
        });
        return response.choices[0].message.content;
      } catch (error) {
        const status = error.status || error.code;
        logEvent({
          event: "llm_call_error",
          request_id: requestId,
          tenant_id: tenantId,
          scenario,
          model: route.model,
          cost_group: route.group,
          fallback_index: fallbackIndex,
          attempt,
          status,
        });
        if (status === 429 || status === "ETIMEDOUT" || status >= 500) {
          await sleep(Math.min(1000 * 2 ** attempt, 6000));
          continue;
        }
        throw error;
      }
    }
  }

  throw new Error(`all LLM routes failed: request_id=${requestId}`);
}
```

## Practical Routing Notes

For AI customer support, keep the first response on stable-official routing and use fallback only when the response can still satisfy the product SLA. For batch content generation, welfare routing can be acceptable because drafts are replayable and reviewed before publication. For data analysis and internal tools, official-transfer routing often balances cost and reliability. For paid SaaS features, log every degraded answer and expose a graceful fallback state to the product layer.

## Suitable and Unsuitable Users

Suitable users: developers, small technical teams, automation builders, SaaS teams, AI support teams, data-analysis workflows, and channel partners with real API volume and basic engineering ability.

Not suitable users: complete beginners, free-only traffic, very low-budget trial users, abusive workloads, or customers who need heavy manual support without technical capacity.

## FAQ

### Can I use the normal OpenAI SDK?

Yes. Use an OpenAI-compatible base URL, keep the API key server-side, and avoid hardcoding model and group decisions into product code.

### Should every 429 trigger fallback?

No. For non-urgent batch work, queue and retry later. For customer-visible flows, fallback can be useful, but it must be bounded and logged.

### Which group should a small team start with?

Start by scenario. Use stable-official for customer-visible and revenue-sensitive flows, official-transfer for routine business workloads, and welfare for replayable drafts or batch automation.

### What logs matter most?

At minimum: `request_id`, `tenant_id`, `scenario`, `model`, `cost_group`, `fallback_index`, `attempt`, `latency_ms`, `status_code`, and `error_type`.

### Is ViralAPI for non-technical users?

No. It is best for users who can self-serve an API integration, understand environment variables, and debug normal SDK/runtime issues.

## Resources

- Website: https://viralapi.ai
- GitHub repository: https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages: https://sxl7530-hashs.github.io/viralapi-examples/2026-08-27-python-node-sdk-error-handling-rate-limit-logs.html
- FAQ: https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- Deep content matrix: https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- Email: miutayoung@gmail.com
- Telegram: viral_8866
- WeChat: viral_8866
