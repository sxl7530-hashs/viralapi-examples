"""No-network multi-model gateway contract for small teams.

The application chooses a business scenario; the gateway owns model order,
budget admission, one deadline, bounded fallback, and structured results.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Callable


class RetryableUpstreamError(RuntimeError):
    pass


class OutputContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class Route:
    models: tuple[str, ...]
    cost_group: str
    deadline_s: float
    allow_fallback: bool


@dataclass(frozen=True)
class Result:
    request_id: str
    scenario: str
    model: str
    cost_group: str
    fallback_index: int
    degraded: bool
    latency_ms: int
    text: str


ROUTES = {
    "support_reply": Route(("CLAUDE_MODEL", "GPT_MODEL"), "stable_official", 8.0, False),
    "content_draft": Route(("GPT_MODEL", "GEMINI_MODEL"), "welfare", 60.0, True),
    "data_analysis": Route(("CLAUDE_MODEL", "GPT_MODEL"), "official_transfer", 90.0, True),
}


def validate_output(text: str) -> str:
    text = text.strip()
    if not text:
        raise OutputContractError("empty output")
    if len(text) > 4_000:
        raise OutputContractError("output exceeds business limit")
    return text


def route_request(
    scenario: str,
    request_id: str,
    remaining_budget_cents: int,
    reserve_cents: int,
    call_model: Callable[[str, float], str],
) -> Result:
    route = ROUTES[scenario]
    if remaining_budget_cents < reserve_cents:
        raise RuntimeError("budget_guard:enqueue_or_handoff")

    started = time.monotonic()
    deadline = started + route.deadline_s
    candidates = route.models if route.allow_fallback else route.models[:1]
    last_error: Exception | None = None

    for fallback_index, model in enumerate(candidates):
        remaining_s = deadline - time.monotonic()
        if remaining_s <= 0.25:
            break
        try:
            text = validate_output(call_model(model, remaining_s))
            return Result(
                request_id=request_id,
                scenario=scenario,
                model=model,
                cost_group=route.cost_group,
                fallback_index=fallback_index,
                degraded=fallback_index > 0,
                latency_ms=int((time.monotonic() - started) * 1000),
                text=text,
            )
        except RetryableUpstreamError as exc:
            last_error = exc

    raise RuntimeError("route_exhausted") from last_error


def demo_call(model: str, timeout_s: float) -> str:
    del timeout_s
    if model == "GPT_MODEL":
        raise RetryableUpstreamError("simulated 503")
    return "validated response from the approved fallback"


if __name__ == "__main__":
    result = route_request("content_draft", "content-20261027-001", 100, 20, demo_call)
    print(json.dumps(asdict(result), sort_keys=True))
