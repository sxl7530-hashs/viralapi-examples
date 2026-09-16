#!/usr/bin/env python3
"""Gemini-first OpenAI-compatible routing with deadlines, retry, and fallback.

Dry run (no network or API key required):
    python3 examples/python/gemini_fallback_circuit_breaker.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from typing import Any

LOG = logging.getLogger("viralapi.gemini_fallback")
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Candidate:
    model: str
    cost_group: str
    max_attempts: int
    per_try_cap_s: float


class CircuitOpen(RuntimeError):
    pass


class CircuitBreaker:
    """Single-process rolling-window breaker keyed by model and cost group."""

    def __init__(
        self,
        threshold: int = 4,
        window_s: float = 60.0,
        cooldown_s: float = 30.0,
    ) -> None:
        self.threshold = threshold
        self.window_s = window_s
        self.cooldown_s = cooldown_s
        self.failures: dict[str, deque[float]] = defaultdict(deque)
        self.open_until: dict[str, float] = {}

    def allow(self, key: str, now: float) -> None:
        if now < self.open_until.get(key, 0.0):
            raise CircuitOpen("route is cooling down")

    def success(self, key: str) -> None:
        self.failures.pop(key, None)
        self.open_until.pop(key, None)

    def failure(self, key: str, now: float) -> None:
        failures = self.failures[key]
        failures.append(now)
        while failures and now - failures[0] > self.window_s:
            failures.popleft()
        if len(failures) >= self.threshold:
            self.open_until[key] = now + self.cooldown_s
            failures.clear()


BREAKER = CircuitBreaker()


def routes() -> list[Candidate]:
    return [
        Candidate(
            os.getenv("PRIMARY_MODEL", "gemini-2.5-flash"),
            os.getenv("PRIMARY_COST_GROUP", "stable_official"),
            2,
            3.0,
        ),
        Candidate(
            os.getenv("FALLBACK_MODEL", "gpt-4.1-mini"),
            os.getenv("FALLBACK_COST_GROUP", "official_transfer"),
            1,
            2.5,
        ),
    ]


def emit(event: str, **fields: Any) -> None:
    # Keep prompts, outputs, credentials, and personal data out of normal logs.
    LOG.info(json.dumps({"event": event, **fields}, ensure_ascii=False))


def classify(exc: Exception) -> tuple[bool, int | None]:
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        return status_code in RETRYABLE_STATUS, status_code
    # Avoid importing the optional SDK solely to classify connection failures.
    if type(exc).__name__ in {"APITimeoutError", "APIConnectionError"}:
        return True, None
    return False, None


def validated_text(response: Any) -> str:
    """Apply the same minimal contract to primary and fallback responses."""
    text = response.choices[0].message.content
    if not text or not text.strip():
        raise ValueError("empty model output")
    return text.strip()


def complete(
    messages: list[dict[str, str]],
    *,
    tenant_id: str,
    scenario: str = "ai_support",
    total_deadline_s: float = 8.0,
    reserve_for_fallback_s: float = 2.5,
    dry_run: bool = False,
) -> str:
    candidates = routes()
    request_id = str(uuid.uuid4())
    if dry_run:
        print(
            json.dumps(
                {
                    "request_id": request_id,
                    "tenant_id": tenant_id,
                    "scenario": scenario,
                    "total_deadline_s": total_deadline_s,
                    "reserve_for_fallback_s": reserve_for_fallback_s,
                    "routes": [asdict(candidate) for candidate in candidates],
                    "retryable_status": sorted(RETRYABLE_STATUS),
                },
                ensure_ascii=False,
            )
        )
        return "dry-run"

    if total_deadline_s <= reserve_for_fallback_s:
        raise ValueError("total deadline must exceed fallback reserve")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install the runtime dependency with: pip install openai") from exc

    client = OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
        max_retries=0,  # This router is the only retry owner.
    )
    deadline = time.monotonic() + total_deadline_s
    last_error: Exception | None = None

    for fallback_index, candidate in enumerate(candidates):
        route_key = f"{candidate.model}:{candidate.cost_group}"
        try:
            BREAKER.allow(route_key, time.monotonic())
        except CircuitOpen as exc:
            last_error = exc
            emit(
                "circuit_skip",
                request_id=request_id,
                tenant_id=tenant_id,
                scenario=scenario,
                model=candidate.model,
                cost_group=candidate.cost_group,
                fallback_index=fallback_index,
                degraded=True,
            )
            continue

        for attempt in range(1, candidate.max_attempts + 1):
            remaining = deadline - time.monotonic()
            reserve = reserve_for_fallback_s if fallback_index == 0 else 0.2
            attempt_timeout = min(candidate.per_try_cap_s, remaining - reserve)
            if attempt_timeout <= 0.2:
                emit(
                    "deadline_budget_exhausted",
                    request_id=request_id,
                    tenant_id=tenant_id,
                    scenario=scenario,
                    model=candidate.model,
                    fallback_index=fallback_index,
                    deadline_remaining_ms=max(0, round(remaining * 1000)),
                )
                break

            started = time.monotonic()
            try:
                response = client.with_options(timeout=attempt_timeout).chat.completions.create(
                    model=candidate.model,
                    messages=messages,
                    temperature=0.2,
                    extra_headers={
                        "X-Request-ID": request_id,
                        "X-Tenant-ID": tenant_id,
                        "X-Scenario": scenario,
                        "X-Cost-Group": candidate.cost_group,
                    },
                )
                text = validated_text(response)
                BREAKER.success(route_key)
                usage = getattr(response, "usage", None)
                emit(
                    "route_success",
                    request_id=request_id,
                    tenant_id=tenant_id,
                    scenario=scenario,
                    model=candidate.model,
                    cost_group=candidate.cost_group,
                    attempt=attempt,
                    fallback_index=fallback_index,
                    degraded=fallback_index > 0,
                    latency_ms=round((time.monotonic() - started) * 1000),
                    deadline_remaining_ms=max(0, round((deadline - time.monotonic()) * 1000)),
                    input_tokens=getattr(usage, "prompt_tokens", None),
                    output_tokens=getattr(usage, "completion_tokens", None),
                )
                return text
            except ValueError as exc:
                # Invalid output may use a tested fallback but should not trip a network breaker.
                last_error = exc
                retryable, status_code = False, None
            except Exception as exc:
                last_error = exc
                retryable, status_code = classify(exc)
                if retryable:
                    BREAKER.failure(route_key, time.monotonic())

            emit(
                "route_failed",
                request_id=request_id,
                tenant_id=tenant_id,
                scenario=scenario,
                model=candidate.model,
                cost_group=candidate.cost_group,
                attempt=attempt,
                fallback_index=fallback_index,
                retryable=retryable,
                status_code=status_code,
                error_type=type(last_error).__name__,
                latency_ms=round((time.monotonic() - started) * 1000),
                deadline_remaining_ms=max(0, round((deadline - time.monotonic()) * 1000)),
                degraded=True,
            )
            if not retryable or attempt == candidate.max_attempts:
                break

            # Full jitter prevents synchronized retry storms and still respects the deadline.
            delay = random.uniform(0.0, min(0.5, 0.1 * (2 ** (attempt - 1))))
            if time.monotonic() + delay + 0.2 >= deadline:
                break
            time.sleep(delay)

    raise RuntimeError(f"all routes exhausted; request_id={request_id}") from last_error


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default="example-tenant")
    parser.add_argument("--scenario", default="ai_support")
    parser.add_argument("--deadline", type=float, default=8.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(
        complete(
            [{"role": "user", "content": "Classify this support ticket and suggest a next action."}],
            tenant_id=args.tenant_id,
            scenario=args.scenario,
            total_deadline_s=args.deadline,
            dry_run=args.dry_run,
        )
    )
