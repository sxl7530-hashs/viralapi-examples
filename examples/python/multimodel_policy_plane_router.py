"""Network-free policy-plane router for Claude, GPT, and Gemini.

The application sends a business route. A versioned policy selects candidates,
reserves fallback time, enforces capability requirements, and emits audit events.
Replace simulated_call with an OpenAI-compatible client in production.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from typing import Callable


class RouteError(RuntimeError):
    pass


class RetryableError(RouteError):
    pass


class ContractError(RouteError):
    pass


@dataclass(frozen=True)
class Candidate:
    alias: str
    family: str
    cost_group: str
    capabilities: frozenset[str]
    max_attempt_s: float


@dataclass(frozen=True)
class Policy:
    version: str
    candidates: tuple[Candidate, ...]
    required_capabilities: frozenset[str]
    total_deadline_s: float
    fallback_reserve_s: float
    max_fallbacks: int


@dataclass(frozen=True)
class Result:
    request_id: str
    business_route: str
    policy_version: str
    selected_alias: str
    model_family: str
    cost_group: str
    fallback_index: int
    degraded: bool
    latency_ms: int
    text: str
    events: tuple[dict[str, object], ...]


CLAUDE = Candidate("claude-support", "claude", "stable_official", frozenset({"text", "long_context"}), 3.2)
GPT = Candidate("gpt-general", "gpt", "official_transfer", frozenset({"text", "json_schema", "tools"}), 3.0)
GEMINI = Candidate("gemini-batch", "gemini", "welfare", frozenset({"text", "json_schema", "multimodal"}), 8.0)

POLICIES = {
    "support_reply": Policy("support-v3", (CLAUDE, GPT), frozenset({"text"}), 7.0, 2.4, 1),
    "content_draft": Policy("content-v5", (GEMINI, GPT), frozenset({"text", "json_schema"}), 20.0, 4.0, 1),
    "analytics_json": Policy("analytics-v2", (GPT, GEMINI), frozenset({"text", "json_schema"}), 12.0, 3.5, 1),
}


def validate(text: str) -> str:
    text = text.strip()
    if not text or len(text) > 4000:
        raise ContractError("business_output_invalid")
    return text


def execute(
    business_route: str,
    request_id: str,
    call: Callable[[Candidate, float], str],
) -> Result:
    policy = POLICIES[business_route]
    started = time.monotonic()
    deadline = started + policy.total_deadline_s
    events: list[dict[str, object]] = []
    eligible = [c for c in policy.candidates if policy.required_capabilities <= c.capabilities]
    if not eligible:
        raise RouteError("no_capable_candidate")

    for fallback_index, candidate in enumerate(eligible[: policy.max_fallbacks + 1]):
        remaining = deadline - time.monotonic()
        reserve = policy.fallback_reserve_s if fallback_index == 0 and len(eligible) > 1 else 0.2
        timeout_s = min(candidate.max_attempt_s, remaining - reserve)
        if timeout_s <= 0:
            break
        event: dict[str, object] = {
            "request_id": request_id,
            "business_route": business_route,
            "policy_version": policy.version,
            "model_alias": candidate.alias,
            "model_family": candidate.family,
            "cost_group": candidate.cost_group,
            "fallback_index": fallback_index,
            "timeout_ms": int(timeout_s * 1000),
        }
        try:
            text = validate(call(candidate, timeout_s))
            event["outcome"] = "success"
            events.append(event)
            return Result(
                request_id, business_route, policy.version, candidate.alias,
                candidate.family, candidate.cost_group, fallback_index,
                fallback_index > 0, int((time.monotonic() - started) * 1000),
                text, tuple(events),
            )
        except (RetryableError, ContractError) as exc:
            event["outcome"] = type(exc).__name__
            events.append(event)

    raise RouteError("route_exhausted")


def simulated_call(mode: str) -> Callable[[Candidate, float], str]:
    def call(candidate: Candidate, timeout_s: float) -> str:
        if timeout_s <= 0:
            raise RetryableError("deadline_exhausted")
        if mode == "primary-503" and candidate.alias in {"claude-support", "gemini-batch", "gpt-general"}:
            mode_state["calls"] += 1
            if mode_state["calls"] == 1:
                raise RetryableError("simulated_503")
        if mode == "primary-invalid":
            mode_state["calls"] += 1
            if mode_state["calls"] == 1:
                return ""
        return f"validated output from {candidate.alias}"

    mode_state = {"calls": 0}
    return call


def self_test() -> None:
    direct = execute("support_reply", "test-direct", simulated_call("healthy"))
    assert direct.selected_alias == "claude-support" and not direct.degraded
    fallback = execute("support_reply", "test-fallback", simulated_call("primary-503"))
    assert fallback.selected_alias == "gpt-general" and fallback.degraded
    invalid = execute("analytics_json", "test-contract", simulated_call("primary-invalid"))
    assert invalid.selected_alias == "gemini-batch" and invalid.degraded
    print("self-test: ok")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--route", choices=sorted(POLICIES), default="support_reply")
    parser.add_argument("--mode", choices=("healthy", "primary-503", "primary-invalid"), default="primary-503")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = execute(args.route, "demo-20261103-001", simulated_call(args.mode))
    print(json.dumps(asdict(result), ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
