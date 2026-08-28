# Python/Node.js SDK in Production: Tenant Rate Limits, Timeout Budgets, and Observable Fallback

ViralAPI is an OpenAI-compatible multi-model API gateway for developers, small teams, and automation workflows. It supports Claude, GPT, and Gemini by scenario, with cost and stability groups for different workloads.

This guide focuses on production boundaries for AI support, content generation, data analysis, internal tools, and SaaS features: tenant concurrency, total deadlines, bounded retries, fallback telemetry, and cost-aware routing.

Use the welfare group (about 15% of official pricing) for replayable batch drafts, official-transfer (about 60%) for routine workloads, and stable-official (about 80%) for customer-visible paths. Choose by budget, stability, and business scenario.

```python
import os, time, uuid
from openai import OpenAI, RateLimitError

client = OpenAI(api_key=os.environ["VIRALAPI_API_KEY"],
                base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
                timeout=12, max_retries=0)

routes = [("claude-sonnet-4", "stable_official"), ("gpt-4o-mini", "official_transfer")]

def complete(tenant_id, messages):
    request_id = str(uuid.uuid4())
    for fallback_index, (model, group) in enumerate(routes):
        try:
            result = client.chat.completions.create(model=model, messages=messages)
            print({"event": "llm_call_ok", "request_id": request_id,
                   "tenant_id": tenant_id, "model": model, "cost_group": group,
                   "fallback_index": fallback_index, "degraded": fallback_index > 0})
            return result.choices[0].message.content
        except RateLimitError:
            if fallback_index == 0:
                time.sleep(1)
    raise RuntimeError(f"all routes failed: request_id={request_id}")
```

Node.js should set an SDK timeout and an application-level deadline. Retry only 408/425/429/5xx and transport timeouts. Fail fast on 400/401/403 and schema errors. In multi-instance deployments, put tenant counters in Redis, a queue, or the gateway; an in-process semaphore is not a global limit.

Every attempt should include `request_id`, `tenant_id`, `feature`, `model`, `cost_group`, `attempt`, `latency_ms`, `status_code`, `degraded`, and `final_status`. This is essential for customer support and for detecting a fallback path that is silently increasing spend.

## Suitable users

Suitable: developers, technical small teams, automation builders, SaaS teams, and channel partners with real volume and basic integration skills. Not suitable: beginners, free-only traffic, very low-budget trials, abusive workloads, or customers requiring heavy manual support.

## FAQ

### How many retries are reasonable?

One or two per candidate with a total deadline. Batch work should queue instead of retrying indefinitely.

### How should tenants be rate limited?

Use a shared counter or queue in production. A local semaphore only protects one process.

### Is fallback success a normal success?

It can be a business success, but telemetry must mark `degraded=true`.

### Which group should I choose?

Stable-official for customer-visible flows, official-transfer for routine workloads, and welfare for replayable drafts. Choose according to budget, stability, and scenario.

Website: https://viralapi.ai
GitHub: https://github.com/sxl7530-hashs/viralapi-examples
FAQ: https://sxl7530-hashs.github.io/viralapi-examples/faq.html
Contact: miutayoung@gmail.com | Telegram/WeChat: viral_8866
