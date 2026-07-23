from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Sequence

from openai import OpenAI

logger = logging.getLogger("viralapi.tenant_budget_router")

@dataclass(frozen=True)
class Route:
    models: list[str]
    group: str
    timeout_seconds: float
    retries: int
    daily_budget_units: int

ROUTES = {
    "support_realtime": Route(["claude-sonnet-4", "gpt-4o-mini"], "stable_official", 18, 1, 5000),
    "content_batch": Route(["gemini-2.5-flash", "claude-sonnet-4"], "welfare_or_official_transfer", 45, 2, 20000),
    "analytics_internal": Route(["gpt-4.1-mini", "claude-sonnet-4"], "official_transfer", 30, 1, 8000),
}

class BudgetStore:
    def used_today(self, tenant_id: str, scenario: str) -> int:
        return 0

    def add_usage(self, tenant_id: str, scenario: str, units: int) -> None:
        pass

def estimate_units(messages: Sequence[dict[str, str]]) -> int:
    return max(1, sum(len(m.get("content", "")) for m in messages) // 4)

def build_client(timeout_seconds: float) -> OpenAI:
    return OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
        timeout=timeout_seconds,
        max_retries=0,
    )

def chat_with_budget(messages: Sequence[dict[str, str]], scenario: str, tenant_id: str, tenant_tier: str, request_id: str, budgets: BudgetStore) -> str:
    route = ROUTES.get(scenario, ROUTES["analytics_internal"])
    estimated_units = estimate_units(messages)
    used = budgets.used_today(tenant_id, scenario)
    if used + estimated_units > route.daily_budget_units and scenario != "support_realtime":
        raise RuntimeError(f"budget exceeded tenant_id={tenant_id} scenario={scenario}")
    client = build_client(route.timeout_seconds)
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
                        "X-Business-Scenario": scenario,
                        "X-Tenant-ID": tenant_id,
                        "X-Tenant-Tier": tenant_tier,
                    },
                )
                budgets.add_usage(tenant_id, scenario, estimated_units)
                logger.info(
                    "llm_success request_id=%s tenant_id=%s tier=%s scenario=%s group=%s model=%s attempt=%d latency_ms=%d units=%d fallback_from=%s",
                    request_id, tenant_id, tenant_tier, scenario, route.group, model, attempt,
                    round((time.monotonic() - started) * 1000), estimated_units, fallback_from,
                )
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                logger.warning("llm_error request_id=%s model=%s attempt=%d error=%s", request_id, model, attempt, type(exc).__name__)
    raise RuntimeError(f"LLM route failed request_id={request_id}") from last_error
