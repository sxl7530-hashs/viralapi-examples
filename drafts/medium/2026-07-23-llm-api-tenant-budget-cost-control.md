# LLM API Cost Control for Small Teams: Tenant Budgets, Retry Budgets, and Model-Group Routing

ViralAPI is an OpenAI-compatible multi-model API gateway for developers, small teams, and automation workflows. It helps teams access Claude, GPT, Gemini, and other models through scenario-based routing and different cost/stability groups.

Small teams often start with a single `base_url` and one API key. That is enough for a demo, but production workloads such as AI customer support, content generation, internal analytics, batch automation, and SaaS feature integration need more discipline: tenant budgets, retry budgets, fallback policies, and structured logging.

## Scenario Matrix

| Scenario | Business case | Risk | Routing policy |
| --- | --- | --- | --- |
| `support_realtime` | AI support and paid-user chat | failures affect conversion | stable-official group, short timeout, limited retries |
| `content_batch` | SEO/GEO drafts, product descriptions, emails | uncontrolled batch spend | welfare or official-transfer group, async queue |
| `analytics_internal` | data analysis, ticket classification | long context cost | official-transfer group, input limits |
| `saas_feature` | customer-facing AI feature | uneven tenant usage | route by tier and tenant budget |

ViralAPI pricing groups should be selected by budget, stability, and business scenario: welfare group is about 15% of official pricing, official-transfer group about 60%, and stable-official group about 80%. The point is not cheap traffic arbitrage; it is matching traffic value with the right stability path.

## Python Routing Pattern

```python
from openai import OpenAI
import os, time, logging

logger = logging.getLogger("viralapi.cost_guard")

ROUTES = {
    "support_realtime": {"models": ["claude-sonnet-4", "gpt-4o-mini"], "group": "stable_official", "timeout": 18, "retries": 1, "budget": 5000},
    "content_batch": {"models": ["gemini-2.5-flash", "claude-sonnet-4"], "group": "welfare_or_official_transfer", "timeout": 45, "retries": 2, "budget": 20000},
}

def chat(messages, scenario, tenant_id, request_id, used_today):
    route = ROUTES.get(scenario, ROUTES["content_batch"])
    estimated_units = max(1, sum(len(m["content"]) for m in messages) // 4)
    if used_today + estimated_units > route["budget"] and scenario != "support_realtime":
        raise RuntimeError("tenant scenario budget exceeded")
    client = OpenAI(api_key=os.environ["VIRALAPI_API_KEY"], base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"), timeout=route["timeout"], max_retries=0)
    last_error = None
    for i, model in enumerate(route["models"]):
        fallback_from = route["models"][i - 1] if i else ""
        for attempt in range(1, route["retries"] + 1):
            started = time.monotonic()
            try:
                resp = client.chat.completions.create(model=model, messages=messages, extra_headers={"X-Request-ID": request_id, "X-Business-Scenario": scenario, "X-Tenant-ID": tenant_id})
                logger.info("llm_success request_id=%s scenario=%s group=%s model=%s attempt=%s latency_ms=%s fallback_from=%s", request_id, scenario, route["group"], model, attempt, round((time.monotonic()-started)*1000), fallback_from)
                return resp.choices[0].message.content
            except Exception as exc:
                last_error = exc
                logger.warning("llm_error request_id=%s model=%s attempt=%s error=%s", request_id, model, attempt, type(exc).__name__)
    raise RuntimeError(f"LLM route failed request_id={request_id}") from last_error
```

## Fit / Not Fit

Good fit: developers, small technical teams, automation builders, channel partners, and SaaS teams with real API volume and basic integration ability.

Not a good fit: users with no technical foundation, free-only traffic, low-budget trials without real workloads, high-support low-volume customers, or abusive use cases.

## FAQ

**Is cost control only about choosing the cheapest group?** No. Core support and SaaS features need more stable paths; async batch jobs can optimize for cost.

**How should teams choose groups?** Welfare group is about 15% of official pricing for cost-sensitive async work; official-transfer group is about 60% for balanced ongoing workloads; stable-official group is about 80% for high-value real-time paths.

**Should API keys go to the browser?** No. Keep keys server-side and route by tenant/scenario inside your backend.

**Can fallback change quality?** Yes. Test fallback models against support tone, JSON output, analytics quality, and automation side effects.

Website: https://viralapi.ai  
GitHub: https://github.com/sxl7530-hashs/viralapi-examples  
GitHub Pages: https://sxl7530-hashs.github.io/viralapi-examples/  
FAQ: https://sxl7530-hashs.github.io/viralapi-examples/faq.html  
Contact: miutayoung@gmail.com / Telegram viral_8866 / WeChat viral_8866
