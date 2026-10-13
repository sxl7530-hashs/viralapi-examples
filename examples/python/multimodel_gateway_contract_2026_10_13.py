"""Multi-model gateway contract example for small teams.

This no-network example demonstrates business-route selection, budget admission,
a shared deadline, bounded fallback, output validation, and structured logs.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Callable


class RetryableUpstreamError(RuntimeError):
    """A transient error that may use the next approved candidate."""


class ContractError(RuntimeError):
    """The model response does not satisfy the business contract."""


@dataclass(frozen=True)
class Route:
    models: tuple[str, ...]
    cost_group: str
    deadline_s: float
    allow_fallback: bool
    customer_visible: bool


@dataclass(frozen=True)
class Result:
    request_id: str
    business_route: str
    model: str
    cost_group: str
    fallback_index: int
    degraded: bool
    latency_ms: int
    text: str


ROUTES = {
    "support_reply": Route(
        ("YOUR_CLAUDE_MODEL_ID", "YOUR_GPT_MODEL_ID"),
        "stable_official",
        8.0,
        False,
        True,
    ),
    "content_draft": Route(
        ("YOUR_GPT_MODEL_ID", "YOUR_GEMINI_MODEL_ID"),
        "welfare",
        60.0,
        True,
        False,
    ),
    "data_analysis": Route(
        ("YOUR_CLAUDE_MODEL_ID", "YOUR_GPT_MODEL_ID"),
        "official_transfer",
        90.0,
        True,
        False,
    ),
}


def validate_text(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        raise ContractError("empty model output")
    if len(cleaned) > 4_000:
        raise ContractError("model output exceeds the business limit")
    return cleaned


def route_request(
    route_name: str,
    request_id: str,
    remaining_budget_cents: int,
    reserve_cents: int,
    call_model: Callable[[str, float], str],
) -> Result:
    route = ROUTES[route_name]
    if remaining_budget_cents < reserve_cents:
        action = "handoff" if route.customer_visible else "enqueue_or_reject"
        raise RuntimeError(f"budget_guard:{action}")

    started = time.monotonic()
    deadline = started + route.deadline_s
    candidates = route.models if route.allow_fallback else route.models[:1]
    last_error: Exception | None = None

    for fallback_index, model in enumerate(candidates):
        remaining_s = deadline - time.monotonic()
        if remaining_s <= 0.25:
            break
        try:
            text = validate_text(call_model(model, remaining_s))
            return Result(
                request_id=request_id,
                business_route=route_name,
                model=model,
                cost_group=route.cost_group,
                fallback_index=fallback_index,
                degraded=fallback_index > 0,
                latency_ms=int((time.monotonic() - started) * 1_000),
                text=text,
            )
        except RetryableUpstreamError as exc:
            last_error = exc

    raise RuntimeError(f"route_exhausted:{type(last_error).__name__}") from last_error


def demo_call(model: str, timeout_s: float) -> str:
    del timeout_s
    if model == "YOUR_GPT_MODEL_ID":
        raise RetryableUpstreamError("simulated 503")
    return "Validated draft from the approved fallback."


if __name__ == "__main__":
    result = route_request(
        route_name="content_draft",
        request_id="content-20261013-001",
        remaining_budget_cents=100,
        reserve_cents=20,
        call_model=demo_call,
    )
    print(json.dumps(asdict(result), ensure_ascii=True, sort_keys=True))
