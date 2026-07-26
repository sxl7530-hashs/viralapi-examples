#!/usr/bin/env python3
"""Production launch probe for ViralAPI OpenAI-compatible LLM routes.

Set VIRALAPI_API_KEY before running. The script is intentionally small so teams can
copy the timeout, retry, fallback, and structured logging pattern into services.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from openai import APIError, APITimeoutError, OpenAI, RateLimitError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("viralapi.launch_probe")


@dataclass(frozen=True)
class Route:
    model: str
    group: str
    timeout: float
    retries: int


ROUTES = {
    "ai_support": [
        Route("claude-3-5-sonnet", "stable-official", 12.0, 1),
        Route("gpt-4o-mini", "official-transfer", 10.0, 0),
    ],
    "content_batch": [
        Route("gemini-1.5-flash", "welfare", 25.0, 2),
        Route("gpt-4o-mini", "official-transfer", 20.0, 1),
    ],
}


def call_with_fallback(messages, scenario: str, tenant_id: str, request_id: str) -> str:
    routes = ROUTES.get(scenario, ROUTES["ai_support"])
    last_error: Exception | None = None

    for route in routes:
        client = OpenAI(
            api_key=os.environ["VIRALAPI_API_KEY"],
            base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
            timeout=route.timeout,
            max_retries=0,
        )
        for attempt in range(route.retries + 1):
            started = time.monotonic()
            try:
                response = client.chat.completions.create(
                    model=route.model,
                    messages=messages,
                    temperature=0.2,
                    extra_headers={
                        "X-Request-ID": request_id,
                        "X-Tenant-ID": tenant_id,
                        "X-Business-Scenario": scenario,
                        "X-Cost-Group": route.group,
                    },
                )
                log.info(
                    "llm_ok request_id=%s tenant=%s scenario=%s model=%s group=%s latency_ms=%d attempt=%d",
                    request_id,
                    tenant_id,
                    scenario,
                    route.model,
                    route.group,
                    int((time.monotonic() - started) * 1000),
                    attempt,
                )
                return response.choices[0].message.content or ""
            except (APITimeoutError, RateLimitError, APIError) as exc:
                last_error = exc
                log.warning(
                    "llm_retry request_id=%s tenant=%s scenario=%s model=%s group=%s error=%s attempt=%d",
                    request_id,
                    tenant_id,
                    scenario,
                    route.model,
                    route.group,
                    type(exc).__name__,
                    attempt,
                )
                time.sleep(min(2 ** attempt, 4))

    raise RuntimeError(f"all ViralAPI routes failed: {last_error}")


if __name__ == "__main__":
    print(
        call_with_fallback(
            [{"role": "user", "content": "Return a one-line launch health check."}],
            scenario=os.getenv("VIRALAPI_SCENARIO", "ai_support"),
            tenant_id=os.getenv("VIRALAPI_TENANT_ID", "demo-tenant"),
            request_id=os.getenv("VIRALAPI_REQUEST_ID", "manual-probe-001"),
        )
    )
