#!/usr/bin/env python3
"""Production launch checklist helper for ViralAPI OpenAI-compatible LLM integrations."""
import os
import time
import logging
from openai import OpenAI, APIError, APITimeoutError, RateLimitError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

client = OpenAI(
    api_key=os.environ["VIRALAPI_API_KEY"],
    base_url=os.environ.get("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
    timeout=float(os.environ.get("VIRALAPI_TIMEOUT", "20")),
)

ROUTES = [
    {"model": os.getenv("PRIMARY_MODEL", "claude-3-5-sonnet"), "group": "stable-official", "retries": 1},
    {"model": os.getenv("BALANCED_MODEL", "gpt-4o-mini"), "group": "official-transfer", "retries": 1},
    {"model": os.getenv("FALLBACK_MODEL", "gemini-1.5-flash"), "group": "welfare", "retries": 0},
]

def complete(messages, tenant_id="demo", scenario="internal-tool"):
    for route in ROUTES:
        for attempt in range(route["retries"] + 1):
            start = time.time()
            try:
                response = client.chat.completions.create(
                    model=route["model"],
                    messages=messages,
                    temperature=0.2,
                    extra_headers={"X-Cost-Group": route["group"], "X-Business-Scenario": scenario},
                )
                logging.info("ok tenant=%s scenario=%s model=%s group=%s latency_ms=%d", tenant_id, scenario, route["model"], route["group"], int((time.time()-start)*1000))
                return response.choices[0].message.content
            except (APITimeoutError, RateLimitError, APIError) as exc:
                logging.warning("fallback tenant=%s model=%s group=%s error=%s attempt=%d", tenant_id, route["model"], route["group"], type(exc).__name__, attempt)
                time.sleep(min(2 ** attempt, 4))
                break
    raise RuntimeError("All ViralAPI model routes failed")

if __name__ == "__main__":
    print(complete([
        {"role": "system", "content": "You are a concise API launch reviewer."},
        {"role": "user", "content": "List three checks before enabling an AI customer support workflow."},
    ]))
