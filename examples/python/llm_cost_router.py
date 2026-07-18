"""Cost-aware OpenAI-compatible LLM router for ViralAPI examples.

Set VIRALAPI_API_KEY in the server environment before running.
"""
from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass

from openai import OpenAI

logger = logging.getLogger("viralapi.cost_router")


@dataclass(frozen=True)
class Route:
    models: list[str]
    group: str
    timeout_seconds: float
    retries: int
    allow_fallback: bool


ROUTES: dict[str, Route] = {
    "support_realtime": Route(
        models=["claude-sonnet-4", "gpt-4o-mini"],
        group="stable_official",
        timeout_seconds=18,
        retries=1,
        allow_fallback=True,
    ),
    "content_batch": Route(
        models=["claude-sonnet-4", "gemini-2.5-flash"],
        group="welfare_or_official_transfer",
        timeout_seconds=45,
        retries=3,
        allow_fallback=True,
    ),
    "analytics_internal": Route(
        models=["claude-sonnet-4", "gpt-4.1-mini"],
        group="official_transfer",
        timeout_seconds=30,
        retries=2,
        allow_fallback=True,
    ),
}


def build_client(timeout_seconds: float) -> OpenAI:
    return OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
        timeout=timeout_seconds,
        max_retries=0,
    )


def chat_for_scenario(
    messages: Sequence[dict[str, str]],
    scenario: str,
    request_id: str,
    tenant_id: str,
) -> str:
    route = ROUTES.get(scenario, ROUTES["analytics_internal"])
    client = build_client(route.timeout_seconds)
    last_error: Exception | None = None

    for model_index, model in enumerate(route.models):
        if model_index > 0 and not route.allow_fallback:
            break
        fallback_from = route.models[model_index - 1] if model_index > 0 else ""
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
                    },
                )
                latency_ms = round((time.monotonic() - started) * 1000)
                logger.info(
                    "llm_success request_id=%s tenant_id=%s scenario=%s group=%s model=%s attempt=%d latency_ms=%d fallback_from=%s",
                    request_id,
                    tenant_id,
                    scenario,
                    route.group,
                    model,
                    attempt,
                    latency_ms,
                    fallback_from,
                )
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                latency_ms = round((time.monotonic() - started) * 1000)
                logger.warning(
                    "llm_error request_id=%s tenant_id=%s scenario=%s group=%s model=%s attempt=%d latency_ms=%d error=%s",
                    request_id,
                    tenant_id,
                    scenario,
                    route.group,
                    model,
                    attempt,
                    latency_ms,
                    type(exc).__name__,
                )
                if attempt < route.retries:
                    delay = min(8.0, 0.5 * (2 ** (attempt - 1))) + random.random() * 0.2
                    time.sleep(delay)

    raise RuntimeError(f"LLM route failed request_id={request_id} scenario={scenario}") from last_error


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(
        chat_for_scenario(
            [{"role": "user", "content": "Summarize this customer support ticket."}],
            scenario="support_realtime",
            request_id="demo-cost-router-001",
            tenant_id="demo-tenant",
        )
    )
