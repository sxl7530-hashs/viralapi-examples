#!/usr/bin/env python3
"""Offline Claude cross-region failure-domain routing policy demo.

No API key or network call is required. Replace FakeTransport with an
OpenAI-compatible client while keeping admission, deadline and validation rules.
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

RETRYABLE = {408, 429, 500, 502, 503, 504}


class UpstreamError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Route:
    name: str
    model: str
    region: str
    failure_domain: str
    cost_group: str
    timeout_s: float
    max_attempts: int


ROUTES = {
    "support": (
        Route("claude-primary", "claude-primary", "cross-region-a", "fd-a", "stable_official", 3.2, 2),
        Route("claude-secondary", "claude-secondary", "cross-region-b", "fd-b", "stable_official", 2.2, 1),
    ),
    "batch": (
        Route("claude-batch", "claude-batch", "cross-region-a", "fd-a", "official_transfer", 12.0, 2),
        Route("gpt-batch-fallback", "gpt-batch-fallback", "cross-region-c", "fd-c", "official_transfer", 8.0, 1),
    ),
}

DEADLINES = {"support": 8.0, "batch": 35.0}
RESERVE_S = {"support": 2.4, "batch": 8.0}


class FakeTransport:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls: dict[str, int] = {}

    def complete(self, route: Route, timeout_s: float, request: dict[str, Any]) -> dict[str, Any]:
        del timeout_s
        self.calls[route.name] = self.calls.get(route.name, 0) + 1
        if self.mode == "primary-503" and route.name in {"claude-primary", "claude-batch"}:
            raise UpstreamError(503, "simulated upstream unavailable")
        if self.mode == "transient" and self.calls[route.name] == 1:
            raise UpstreamError(429, "simulated rate limit")
        if self.mode == "invalid-primary" and route.name in {"claude-primary", "claude-batch"}:
            return {"choices": []}
        return {
            "id": "chatcmpl-demo",
            "choices": [{"message": {"role": "assistant", "content": "validated response"}}],
            "usage": {"prompt_tokens": len(str(request)), "completion_tokens": 3},
        }


def valid_chat_completion(payload: dict[str, Any]) -> bool:
    try:
        return bool(payload["choices"][0]["message"]["content"].strip())
    except (KeyError, IndexError, TypeError, AttributeError):
        return False


def emit(event: str, **fields: Any) -> None:
    safe = {"event": event, **fields}
    print(json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def route_request(scenario: str, transport: FakeTransport) -> dict[str, Any]:
    request_id = str(uuid.uuid4())
    started = time.monotonic()
    deadline = started + DEADLINES[scenario]
    tried_domains: set[str] = set()
    last_error = "none"
    request = {"messages": [{"role": "user", "content": "redacted-demo"}]}

    for route_index, route in enumerate(ROUTES[scenario]):
        if route.failure_domain in tried_domains:
            emit("route_skipped", request_id=request_id, reason="same_failure_domain", route=route.name)
            continue
        tried_domains.add(route.failure_domain)

        for attempt in range(1, route.max_attempts + 1):
            now = time.monotonic()
            reserve = RESERVE_S[scenario] if route_index == 0 else 0.2
            remaining = deadline - now
            if remaining <= reserve + 0.1:
                last_error = "deadline_exhausted"
                break
            timeout_s = min(route.timeout_s, remaining - reserve)
            attempt_started = time.monotonic()
            try:
                response = transport.complete(route, timeout_s, request)
                if not valid_chat_completion(response):
                    raise ValueError("invalid OpenAI-compatible response")
                emit(
                    "route_success",
                    request_id=request_id,
                    scenario=scenario,
                    route=route.name,
                    model=route.model,
                    region=route.region,
                    failure_domain=route.failure_domain,
                    cost_group=route.cost_group,
                    attempt=attempt,
                    fallback_index=route_index,
                    degraded=route_index > 0,
                    latency_ms=round((time.monotonic() - attempt_started) * 1000),
                    deadline_remaining_ms=max(0, round((deadline - time.monotonic()) * 1000)),
                    prompt_hash="demo-not-content",
                )
                return response
            except Exception as exc:
                status = getattr(exc, "status", None)
                retryable = status in RETRYABLE
                last_error = type(exc).__name__
                emit(
                    "route_failed",
                    request_id=request_id,
                    scenario=scenario,
                    route=route.name,
                    failure_domain=route.failure_domain,
                    attempt=attempt,
                    status_code=status,
                    retryable=retryable,
                    error_type=last_error,
                    degraded=True,
                )
                if isinstance(exc, ValueError) or not retryable or attempt == route.max_attempts:
                    break
                # The gateway is the only retry owner; SDK retries should be disabled.
                time.sleep(min(0.05 * (2 ** (attempt - 1)), max(0.0, deadline - time.monotonic() - reserve)))

    raise RuntimeError(f"all failure domains exhausted: {last_error}")


def self_test() -> None:
    assert valid_chat_completion(route_request("support", FakeTransport("ok")))
    assert valid_chat_completion(route_request("support", FakeTransport("transient")))
    assert valid_chat_completion(route_request("support", FakeTransport("primary-503")))
    assert valid_chat_completion(route_request("support", FakeTransport("invalid-primary")))
    print("SELF_TEST_OK")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=sorted(ROUTES), default="support")
    parser.add_argument("--mode", choices=["ok", "transient", "primary-503", "invalid-primary"], default="ok")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        print(json.dumps(route_request(args.scenario, FakeTransport(args.mode)), indent=2))
