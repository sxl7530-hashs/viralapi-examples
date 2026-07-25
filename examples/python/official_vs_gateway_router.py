from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Sequence

from openai import OpenAI

logger = logging.getLogger("viralapi.official_vs_gateway")

@dataclass(frozen=True)
class Route:
    models: list[str]
    group: str
    timeout_seconds: float
    retries: int
    max_budget_units: int

ROUTES = {
    "ai_support_realtime": Route(["claude-sonnet-4", "gpt-4o-mini"], "stable_official", 18, 1, 4000),
    "content_generation_batch": Route(["gemini-2.5-flash", "claude-sonnet-4"], "welfare_or_official_transfer", 45, 2, 20000),
    "internal_data_analysis": Route(["gpt-4.1-mini", "claude-sonnet-4"], "official_transfer", 30, 1, 8000),
}

def estimate_units(messages: Sequence[dict[str, str]]) -> int:
    return max(1, sum(len(message.get("content", "")) for message in messages) // 4)

def build_client(route: Route) -> OpenAI:
    return OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
        timeout=route.timeout_seconds,
        max_retries=0,
    )

def call_gateway(messages: Sequence[dict[str, str]], scenario: str, tenant_id: str, request_id: str) -> str:
    route = ROUTES.get(scenario, ROUTES["internal_data_analysis"])
    estimated_units = estimate_units(messages)
    if estimated_units > route.max_budget_units:
        raise ValueError(f"request too large request_id={request_id} units={estimated_units}")

    client = build_client(route)
    last_error: Exception | None = None

    for model_index, model in enumerate(route.models):
        fallback_from = route.models[model_index - 1] if model_index else ""
        for attempt in range(1, route.retries + 1):
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
                logger.info(
                    "llm_success request_id=%s tenant_id=%s scenario=%s model=%s group=%s attempt=%d units=%d fallback_from=%s latency_ms=%d",
                    request_id,
                    tenant_id,
                    scenario,
                    model,
                    route.group,
                    attempt,
                    estimated_units,
                    fallback_from,
                    round((time.monotonic() - started) * 1000),
                )
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "llm_error request_id=%s tenant_id=%s scenario=%s model=%s attempt=%d error=%s",
                    request_id,
                    tenant_id,
                    scenario,
                    model,
                    attempt,
                    type(exc).__name__,
                )

    raise RuntimeError(f"all gateway routes failed request_id={request_id}") from last_error
