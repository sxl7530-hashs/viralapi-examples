"""Claude cross-region OpenAI-compatible probe with bounded fallback."""
import os, time
from openai import OpenAI

RETRYABLE = {408, 429, 500, 502, 503, 504}
ROUTES = [("claude-sonnet-4", "stable_official"), ("gpt-4.1-mini", "official_transfer")]

def complete(messages, total_deadline=12.0):
    client = OpenAI(api_key=os.environ["VIRALAPI_API_KEY"],
                    base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
                    max_retries=0)
    deadline = time.monotonic() + total_deadline
    for index, (model, cost_group) in enumerate(ROUTES):
        for attempt in range(1, 3):
            remaining = deadline - time.monotonic()
            if remaining <= 0.5:
                raise TimeoutError("total deadline exhausted")
            try:
                result = client.with_options(timeout=min(8.0, remaining - 0.2)).chat.completions.create(
                    model=model, messages=messages,
                    extra_headers={"X-Request-ID": "probe", "X-Cost-Group": cost_group})
                print({"model": model, "cost_group": cost_group,
                       "attempt": attempt, "degraded": index > 0})
                return result.choices[0].message.content or ""
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if status not in RETRYABLE or attempt == 2:
                    break
                time.sleep(min(0.5, max(0.0, remaining / 4)))
    raise RuntimeError("all routes exhausted")
