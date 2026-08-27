"""Production-style ViralAPI SDK wrapper with bounded retry and logs.

Set VIRALAPI_API_KEY before running. The base URL remains OpenAI-compatible.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Iterable

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError


@dataclass(frozen=True)
class Route:
    model: str
    cost_group: str
    timeout: float


ROUTES: dict[str, list[Route]] = {
    "ai_support": [
        Route("claude-sonnet-4", "stable_official", 12),
        Route("gpt-4o-mini", "official_transfer", 10),
    ],
    "content_batch": [
        Route("gemini-2.5-flash", "welfare", 25),
        Route("gpt-4o-mini", "official_transfer", 20),
    ],
}

RETRYABLE_ERRORS = (APIConnectionError, APITimeoutError, RateLimitError)


def build_client() -> OpenAI:
    return OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.environ.get("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
        timeout=float(os.environ.get("VIRALAPI_TIMEOUT", "20")),
    )


def log_event(**fields: object) -> None:
    print(json.dumps(fields, ensure_ascii=False, sort_keys=True))


def complete_with_fallback(
    *,
    client: OpenAI,
    tenant_id: str,
    scenario: str,
    messages: Iterable[dict[str, str]],
) -> str:
    request_id = str(uuid.uuid4())
    routes = ROUTES[scenario]
    message_list = list(messages)

    for fallback_index, route in enumerate(routes):
        for attempt in range(1, 3):
            started = time.monotonic()
            try:
                response = client.chat.completions.create(
                    model=route.model,
                    messages=message_list,
                    timeout=route.timeout,
                )
                log_event(
                    event="llm_call_ok",
                    request_id=request_id,
                    tenant_id=tenant_id,
                    scenario=scenario,
                    model=route.model,
                    cost_group=route.cost_group,
                    fallback_index=fallback_index,
                    attempt=attempt,
                    latency_ms=round((time.monotonic() - started) * 1000),
                )
                return response.choices[0].message.content or ""
            except RETRYABLE_ERRORS as exc:
                log_event(
                    event="llm_call_retryable_error",
                    request_id=request_id,
                    tenant_id=tenant_id,
                    scenario=scenario,
                    model=route.model,
                    cost_group=route.cost_group,
                    fallback_index=fallback_index,
                    attempt=attempt,
                    error_type=exc.__class__.__name__,
                )
                time.sleep(min(2**attempt, 6))
            except APIStatusError as exc:
                log_event(
                    event="llm_call_status_error",
                    request_id=request_id,
                    tenant_id=tenant_id,
                    scenario=scenario,
                    model=route.model,
                    cost_group=route.cost_group,
                    fallback_index=fallback_index,
                    attempt=attempt,
                    status_code=exc.status_code,
                )
                if exc.status_code >= 500:
                    break
                raise

    raise RuntimeError(f"all LLM routes failed: request_id={request_id}")


if __name__ == "__main__":
    client = build_client()
    answer = complete_with_fallback(
        client=client,
        tenant_id="tenant_demo",
        scenario="content_batch",
        messages=[{"role": "user", "content": "Draft a concise support macro."}],
    )
    print(answer)
