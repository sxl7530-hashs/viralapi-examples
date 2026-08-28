"""Tenant-aware OpenAI-compatible client with a global deadline.

This example keeps retry and fallback budgets explicit. In production, replace
TenantLimiter with Redis, a queue, or gateway-side rate limiting so limits are
shared across service instances.
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


ROUTES = {
    "support": [
        Route("claude-sonnet-4", "stable_official", 10),
        Route("gpt-4o-mini", "official_transfer", 8),
    ],
    "batch": [
        Route("gemini-2.5-flash", "welfare", 20),
        Route("gpt-4o-mini", "official_transfer", 12),
    ],
}

RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError)


class TenantLimiter:
    """Small-process demo; use a shared limiter for multi-instance services."""

    def __init__(self, per_tenant: int = 2) -> None:
        self.per_tenant = per_tenant
        self.active: dict[str, int] = {}

    def acquire(self, tenant_id: str) -> bool:
        current = self.active.get(tenant_id, 0)
        if current >= self.per_tenant:
            return False
        self.active[tenant_id] = current + 1
        return True

    def release(self, tenant_id: str) -> None:
        self.active[tenant_id] = max(self.active.get(tenant_id, 1) - 1, 0)


def log_event(**fields: object) -> None:
    print(json.dumps(fields, ensure_ascii=False, sort_keys=True))


def complete(*, client: OpenAI, limiter: TenantLimiter, tenant_id: str,
             scenario: str, messages: Iterable[dict[str, str]]) -> str:
    request_id = str(uuid.uuid4())
    if not limiter.acquire(tenant_id):
        log_event(event="tenant_rate_limited", request_id=request_id, tenant_id=tenant_id)
        raise RuntimeError("tenant concurrency limit reached")

    deadline = time.monotonic() + (25 if scenario == "support" else 90)
    try:
        for fallback_index, route in enumerate(ROUTES[scenario]):
            for attempt in range(1, 3):
                if time.monotonic() >= deadline:
                    break
                started = time.monotonic()
                try:
                    response = client.chat.completions.create(
                        model=route.model,
                        messages=list(messages),
                        timeout=min(route.timeout, max(deadline - time.monotonic(), 1)),
                    )
                    log_event(event="llm_call_ok", request_id=request_id,
                              tenant_id=tenant_id, scenario=scenario,
                              model=route.model, cost_group=route.cost_group,
                              fallback_index=fallback_index, attempt=attempt,
                              degraded=fallback_index > 0,
                              latency_ms=round((time.monotonic() - started) * 1000))
                    return response.choices[0].message.content or ""
                except RETRYABLE as exc:
                    log_event(event="llm_call_retryable", request_id=request_id,
                              tenant_id=tenant_id, model=route.model,
                              attempt=attempt, error_type=type(exc).__name__)
                    if attempt < 2:
                        time.sleep(min(2**attempt, 4))
                except APIStatusError as exc:
                    log_event(event="llm_call_status_error", request_id=request_id,
                              tenant_id=tenant_id, model=route.model,
                              status_code=exc.status_code)
                    if exc.status_code < 500:
                        raise
        raise RuntimeError(f"all routes failed: request_id={request_id}")
    finally:
        limiter.release(tenant_id)


if __name__ == "__main__":
    sdk = OpenAI(api_key=os.environ["VIRALAPI_API_KEY"],
                 base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
                 timeout=15, max_retries=0)
    print(complete(client=sdk, limiter=TenantLimiter(), tenant_id="demo",
                   scenario="support",
                   messages=[{"role": "user", "content": "Summarize this ticket."}]))
