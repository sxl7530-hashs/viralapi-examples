#!/usr/bin/env python3
"""OpenAI-compatible multi-model router with per-scenario deadlines and a circuit breaker."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOG = logging.getLogger("multimodel-slo-router")

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Route:
    model: str
    cost_group: str
    attempt_timeout_seconds: float


ROUTES = {
    "ai_support": [
        Route("claude-sonnet", "stable_official", 7.0),
        Route("gpt-mini", "official_transfer", 5.0),
    ],
    "saas_feature": [
        Route("gpt-primary", "stable_official", 8.0),
        Route("claude-sonnet", "stable_official", 6.0),
    ],
    "batch_content": [
        Route("gemini-flash", "welfare", 25.0),
        Route("gpt-mini", "official_transfer", 20.0),
    ],
}


class CircuitBreaker:
    """Open a route after repeated transient failures within a rolling window."""

    def __init__(self, threshold: int = 5, window_seconds: int = 60, cooldown_seconds: int = 30):
        self.threshold = threshold
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self.failures: dict[str, deque[float]] = defaultdict(deque)
        self.open_until: dict[str, float] = {}

    def allow(self, key: str, now: float) -> bool:
        return now >= self.open_until.get(key, 0)

    def success(self, key: str) -> None:
        self.failures.pop(key, None)
        self.open_until.pop(key, None)

    def failure(self, key: str, now: float) -> None:
        events = self.failures[key]
        events.append(now)
        while events and now - events[0] > self.window_seconds:
            events.popleft()
        if len(events) >= self.threshold:
            self.open_until[key] = now + self.cooldown_seconds
            events.clear()


BREAKER = CircuitBreaker()
CLIENT = OpenAI(
    api_key=os.environ["VIRALAPI_API_KEY"],
    base_url=os.environ["VIRALAPI_BASE_URL"],
    max_retries=0,
)


def emit(event: str, **fields: Any) -> None:
    LOG.info(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str))


def status_code(exc: Exception) -> int | None:
    return exc.status_code if isinstance(exc, APIStatusError) else None


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError)):
        return True
    return isinstance(exc, APIStatusError) and exc.status_code in RETRYABLE_STATUS


def complete(
    messages: list[dict[str, str]],
    *,
    tenant_id: str,
    scenario: str,
    deadline_seconds: float,
) -> str:
    if scenario not in ROUTES:
        raise ValueError(f"unsupported scenario: {scenario}")

    request_id = str(uuid.uuid4())
    deadline = time.monotonic() + deadline_seconds
    last_error: Exception | None = None

    for fallback_index, route in enumerate(ROUTES[scenario]):
        key = f"{route.model}:{route.cost_group}"
        now = time.monotonic()
        if not BREAKER.allow(key, now):
            emit("circuit_open", request_id=request_id, tenant_id=tenant_id,
                 scenario=scenario, model=route.model, cost_group=route.cost_group)
            continue

        remaining = deadline - now
        if remaining <= 0:
            break
        timeout = min(route.attempt_timeout_seconds, remaining)
        started = time.monotonic()

        try:
            response = CLIENT.with_options(timeout=timeout).chat.completions.create(
                model=route.model,
                messages=messages,
                extra_headers={
                    "X-Request-ID": request_id,
                    "X-Tenant-ID": tenant_id,
                    "X-Scenario": scenario,
                    "X-Cost-Group": route.cost_group,
                },
            )
            BREAKER.success(key)
            usage = response.usage
            emit(
                "success",
                request_id=request_id,
                tenant_id=tenant_id,
                scenario=scenario,
                model=route.model,
                cost_group=route.cost_group,
                fallback_index=fallback_index,
                degraded=fallback_index > 0,
                latency_ms=round((time.monotonic() - started) * 1000),
                prompt_tokens=getattr(usage, "prompt_tokens", None),
                completion_tokens=getattr(usage, "completion_tokens", None),
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            last_error = exc
            retryable = is_retryable(exc)
            if retryable:
                BREAKER.failure(key, time.monotonic())
            emit(
                "route_failed",
                request_id=request_id,
                tenant_id=tenant_id,
                scenario=scenario,
                model=route.model,
                cost_group=route.cost_group,
                fallback_index=fallback_index,
                latency_ms=round((time.monotonic() - started) * 1000),
                status_code=status_code(exc),
                error_type=type(exc).__name__,
                retryable=retryable,
                deadline_remaining_ms=max(0, round((deadline - time.monotonic()) * 1000)),
            )
            if not retryable:
                raise

    raise RuntimeError(f"all routes exhausted: {type(last_error).__name__ if last_error else 'deadline'}")


if __name__ == "__main__":
    answer = complete(
        [{"role": "user", "content": "Classify this support ticket and suggest a next action."}],
        tenant_id="example-tenant",
        scenario="ai_support",
        deadline_seconds=12,
    )
    print(answer)
