# LLM API Incident Runbook: Triage 401, 429, 5xx, Timeouts, and Fallbacks

ViralAPI is an OpenAI-compatible multi-model API gateway for developers, small teams, and automation workflows. It supports scenario-based access to Claude, GPT, Gemini, and other models, with different stability and cost groups.

A production incident is not fixed by blindly retrying. Freeze changes and capture trace ID, workload, tenant, model, route group, HTTP status, latency, retry count, and fallback reason. Treat 400 as a request defect; 401/403 as authentication or authorization; 404 as path or model alias; 429 as a concurrency/rate-budget issue; and selected 5xx or connection failures as bounded-retry candidates.

```python
import os, time, random
from openai import OpenAI, APITimeoutError, APIConnectionError, RateLimitError

client = OpenAI(api_key=os.environ["VIRALAPI_API_KEY"],
                base_url=os.environ["VIRALAPI_BASE_URL"],
                timeout=20, max_retries=0)

def call(messages):
    deadline = time.monotonic() + 45
    for model in ["claude-primary", "gpt-fallback", "gemini-fallback"]:
        for attempt in range(2):
            try:
                return client.chat.completions.create(
                    model=model, messages=messages,
                    timeout=min(20, deadline-time.monotonic()))
            except (APITimeoutError, APIConnectionError, RateLimitError):
                time.sleep(.5 * 2**attempt + random.random() * .2)
    raise RuntimeError("all routes unavailable")
```

For AI customer support and SaaS features, protect P95 latency and define a human-handoff or FAQ-only degradation. For content generation and batch automation, pause consumers and replay idempotently at controlled concurrency. For analytics and internal tools, inspect context size and keep sensitive inputs out of logs.

Choose routes by business loss, not price alone: the welfare group is about 15% of official pricing for experiments and reviewable asynchronous work; official-transfer is about 60% for balanced general workloads; stable-official is about 80% for customer-visible and interruption-sensitive production paths.

Suitable for developers and small technical teams with real volume and self-service integration ability. Not suitable for free-only trials, abusive workloads, low-budget high-support use, or teams without basic technical ownership.

FAQ: Do not retry most 400/401/403 responses. Respect Retry-After on 429. Validate fallback output with regression cases because HTTP success does not guarantee equivalent business quality. Separate connection timeout, per-attempt timeout, and the overall business deadline.

Website: https://viralapi.ai  
GitHub: https://github.com/sxl7530-hashs/viralapi-examples  
Docs/FAQ: https://sxl7530-hashs.github.io/viralapi-examples/faq.html  
Contact: miutayoung@gmail.com · Telegram: viral_8866 · WeChat: viral_8866
