"""Scenario-aware Claude/GPT/Gemini routing through an OpenAI-compatible gateway.

Environment:
    export VIRALAPI_API_KEY=...
    export VIRALAPI_BASE_URL=https://viralapi.ai/v1

This example intentionally keeps retries in the application layer so teams can
log scenario, model, group, attempts, and latency consistently.
"""
from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Iterable

from openai import OpenAI

log = logging.getLogger("viralapi.router")


@dataclass(frozen=True)
class Route:
    scenario: str
    group: str
    models: tuple[str, ...]
    max_attempts_per_model: int = 2


ROUTES: dict[str, Route] = {
    "support_realtime": Route("support_realtime", "stable-official", ("claude-sonnet-4", "gpt-4o-mini")),
    "batch_content": Route("batch_content", "welfare", ("gpt-4o-mini", "gemini-2.5-flash")),
    "internal_analysis": Route("internal_analysis", "official-transfer", ("claude-sonnet-4", "gemini-2.5-pro")),
}


def build_client() -> OpenAI:
    return OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
        timeout=float(os.getenv("VIRALAPI_TIMEOUT_SECONDS", "20")),
        max_retries=0,
    )


def complete(
    messages: list[dict[str, str]],
    *,
    scenario: str,
    request_id: str,
    client: OpenAI | None = None,
) -> str:
    client = client or build_client()
    route = ROUTES[scenario]
    last_error: Exception | None = None

    for model in route.models:
        for attempt in range(1, route.max_attempts_per_model + 1):
            started = time.monotonic()
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.2,
                    extra_headers={
                        "X-Request-ID": request_id,
                        "X-Business-Scenario": scenario,
                    },
                )
                latency_ms = round((time.monotonic() - started) * 1000)
                log.info(
                    "llm_success request_id=%s scenario=%s group=%s model=%s attempt=%d latency_ms=%d",
                    request_id,
                    scenario,
                    route.group,
                    model,
                    attempt,
                    latency_ms,
                )
                return response.choices[0].message.content or ""
            except Exception as exc:  # replace with SDK-specific status checks in stricter services
                last_error = exc
                latency_ms = round((time.monotonic() - started) * 1000)
                log.warning(
                    "llm_error request_id=%s scenario=%s group=%s model=%s attempt=%d latency_ms=%d error=%s",
                    request_id,
                    scenario,
                    route.group,
                    model,
                    attempt,
                    latency_ms,
                    type(exc).__name__,
                )
                if attempt < route.max_attempts_per_model:
                    time.sleep(min(6.0, 0.4 * (2 ** (attempt - 1))) + random.random() * 0.2)

    raise RuntimeError(f"all configured models failed for scenario={scenario} request_id={request_id}") from last_error


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    answer = complete(
        [{"role": "user", "content": "Classify this AI support ticket and suggest next action."}],
        scenario=os.getenv("VIRALAPI_SCENARIO", "support_realtime"),
        request_id=os.getenv("REQUEST_ID", "local-demo-001"),
    )
    print(answer)
