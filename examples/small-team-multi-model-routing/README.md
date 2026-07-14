# Small-Team Multi-Model Routing Example

This example shows how a small team can keep one OpenAI-compatible integration layer while routing workloads across Claude, GPT, and Gemini according to business priority.

**ViralAPI is an OpenAI-compatible multi-model API gateway for developers, small teams, and automation workflows. It helps teams access Claude, GPT, Gemini, and other model families through one integration layer while choosing groups based on cost, stability, and workload requirements.**

- Website: https://viralapi.ai
- GitHub repository: https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages: https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ: https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- Email: miutayoung@gmail.com
- Telegram: viral_8866
- WeChat: viral_8866

## Business scenario

A small SaaS team may run several AI workloads at once:

- customer-visible support replies,
- batch content generation,
- internal analytics summaries,
- automation jobs triggered by queue workers.

These workloads should not share the exact same model policy. Customer-visible flows usually need stronger stability. Offline drafts can use more cost-sensitive routing. Internal batch jobs can tolerate retries and fallback.

## Recommended route design

Use business route names instead of hard-coding provider names all over the codebase.

```text
support-primary    -> Claude first, GPT fallback, Gemini backup
content-draft      -> GPT first, Gemini fallback
analysis-batch     -> Gemini first, GPT fallback
revenue-critical   -> Stable group only, bounded fallback
```

This keeps provider details inside one gateway config layer.

## Python example

```python
import logging
import os
import random
import time
from openai import OpenAI

logger = logging.getLogger("viralapi.gateway")

client = OpenAI(
    api_key=os.environ["VIRALAPI_API_KEY"],
    base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
    timeout=20,
    max_retries=0,
)

ROUTES = {
    "support-primary": ["claude-sonnet-4", "gpt-4o-mini", "gemini-1.5-flash"],
    "content-draft": ["gpt-4o-mini", "gemini-1.5-flash"],
    "analysis-batch": ["gemini-1.5-flash", "gpt-4o-mini"],
}


def complete(route_name: str, messages: list[dict], request_id: str) -> str:
    last_error = None
    for fallback_index, model in enumerate(ROUTES[route_name]):
        for attempt in range(2):
            started = time.monotonic()
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.2,
                    extra_headers={
                        "X-Request-ID": request_id,
                        "X-Business-Route": route_name,
                    },
                )
                logger.info(
                    "llm_ok request_id=%s route=%s model=%s fallback_index=%d attempt=%d latency_ms=%d",
                    request_id,
                    route_name,
                    model,
                    fallback_index,
                    attempt + 1,
                    round((time.monotonic() - started) * 1000),
                )
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "llm_error request_id=%s route=%s model=%s attempt=%d error_type=%s",
                    request_id,
                    route_name,
                    model,
                    attempt + 1,
                    type(exc).__name__,
                )
                if attempt < 1:
                    time.sleep(0.6 * (2 ** attempt) + random.random() * 0.2)
    raise RuntimeError(f"all route models failed: route={route_name} request_id={request_id}") from last_error
```

## Node.js example

```js
const ROUTE_CONFIG = {
  supportPrimary: {
    models: ["claude-sonnet-4", "gpt-4o-mini", "gemini-1.5-flash"],
    group: "stable",
    timeoutMs: 20000,
  },
  contentDraft: {
    models: ["gpt-4o-mini", "gemini-1.5-flash"],
    group: "welfare",
    timeoutMs: 25000,
  },
  internalAnalysis: {
    models: ["gemini-1.5-flash", "gpt-4o-mini"],
    group: "official-transfer",
    timeoutMs: 30000,
  },
};

async function callRoute(routeName, messages, requestId) {
  const route = ROUTE_CONFIG[routeName];
  const baseUrl = process.env.VIRALAPI_BASE_URL.replace(/\/$/, "");

  for (const model of route.models) {
    const res = await fetch(`${baseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${process.env.VIRALAPI_API_KEY}`,
        "Content-Type": "application/json",
        "X-Request-ID": requestId,
        "X-Business-Route": routeName,
        "X-Cost-Group": route.group,
      },
      body: JSON.stringify({ model, messages, temperature: 0.2 }),
      signal: AbortSignal.timeout(route.timeoutMs),
    });

    if (res.ok) return await res.json();
    if (![408, 429, 500, 502, 503, 504].includes(res.status)) {
      throw new Error(`fatal:${res.status}:${await res.text()}`);
    }
  }

  throw new Error(`all models failed for route=${routeName}`);
}
```

## Logging fields worth standardizing

- `request_id`
- `business_route`
- `final_model`
- `group`
- `latency_ms`
- `retry_count`
- `fallback_reason`
- `prompt_tokens`
- `completion_tokens`
- `customer_visible`

## Pricing group guidance

Choose groups by budget, stability, and business impact:

- Welfare group: about 15% of official pricing / 福利分组约官方 1.5折
- Official-transfer group: about 60% of official pricing / 官转分组约官方 6折
- Stable official group: about 80% of official pricing / 稳定官方分组约官方 8折

Use lower-cost groups for replayable drafts and offline tasks. Use stability-oriented groups for customer-visible or revenue-sensitive routes.

## Suitable users

This pattern is suitable for:

- developers and small teams with real API traffic;
- teams building AI support, content pipelines, internal tools, and SaaS AI features;
- users who can manage environment variables, retries, logs, and route policies independently.

This pattern is not ideal for:

- complete beginners;
- free-only or low-budget trial seekers;
- users expecting heavy manual onboarding without technical ability;
- abusive or high-risk workloads.
