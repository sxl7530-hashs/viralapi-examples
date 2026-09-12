from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Sequence

from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("viralapi.route")


@dataclass(frozen=True)
class Route:
    models: tuple[str, ...]
    group: str
    timeout_seconds: float
    retries: int
    max_input_chars: int


ROUTES = {
    "ai_support": Route(("claude-sonnet-4", "gpt-4o-mini"), "stable_official", 18, 1, 12000),
    "content_batch": Route(("gemini-2.5-flash", "claude-sonnet-4"), "welfare_or_official_transfer", 45, 2, 50000),
    "data_analysis": Route(("gpt-4.1-mini", "claude-sonnet-4"), "official_transfer", 30, 1, 30000),
}


def run(messages: Sequence[dict[str, str]], scenario: str, request_id: str, tenant_id: str) -> str:
    route = ROUTES.get(scenario, ROUTES["data_analysis"])
    input_chars = sum(len(message.get("content", "")) for message in messages)
    if input_chars > route.max_input_chars:
        raise ValueError(f"input_budget_exceeded request_id={request_id}")

    client = OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.environ["VIRALAPI_BASE_URL"],
        timeout=route.timeout_seconds,
        max_retries=0,
    )
    last_error: Exception | None = None
    for model_index, model in enumerate(route.models):
        for attempt in range(1, route.retries + 2):
            started = time.monotonic()
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=list(messages),
                    temperature=0.2,
                    extra_headers={
                        "X-Request-ID": request_id,
                        "X-Tenant-ID": tenant_id,
                        "X-Business-Scenario": scenario,
                    },
                )
                log.info(
                    "llm_success request_id=%s tenant_id=%s scenario=%s model=%s group=%s attempt=%d fallback=%s latency_ms=%d",
                    request_id,
                    tenant_id,
                    scenario,
                    model,
                    route.group,
                    attempt,
                    model_index > 0,
                    round((time.monotonic() - started) * 1000),
                )
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                log.warning(
                    "llm_error request_id=%s scenario=%s model=%s attempt=%d error=%s",
                    request_id,
                    scenario,
                    model,
                    attempt,
                    type(exc).__name__,
                )
    raise RuntimeError(f"all_routes_failed request_id={request_id}") from last_error


if __name__ == "__main__":
    print(run([{"role": "user", "content": "Summarize this ticket."}], "ai_support", "demo-001", "demo-tenant"))
