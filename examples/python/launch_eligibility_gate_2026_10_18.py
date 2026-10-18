#!/usr/bin/env python3
"""No-network launch gate for OpenAI-compatible LLM API integrations."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any


RETRYABLE = {408, 429, 500, 502, 503, 504}
AUTH_OR_CONTRACT = {400, 401, 403, 404, 422}


@dataclass(frozen=True)
class Policy:
    scenario: str
    total_deadline_ms: int
    max_attempts: int
    allow_fallback: bool
    require_idempotency: bool


POLICIES = {
    "support": Policy("support", 8000, 2, True, False),
    "saas": Policy("saas", 15000, 2, True, False),
    "batch": Policy("batch", 60000, 2, True, True),
    "write": Policy("write", 10000, 1, False, True),
}


def classify(status: int | None, timed_out: bool = False) -> str:
    if timed_out:
        return "timeout"
    if status in RETRYABLE:
        return "transient"
    if status in AUTH_OR_CONTRACT:
        return "auth_or_contract"
    if status is None:
        return "unknown"
    return "other"


def validate_output(result: Any) -> tuple[bool, str]:
    if not isinstance(result, dict):
        return False, "output_not_object"
    if not isinstance(result.get("answer"), str) or not result["answer"].strip():
        return False, "missing_answer"
    return True, "ok"


def evaluate(policy: Policy, attempts: list[dict[str, Any]]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    fallback_used = False
    final: dict[str, Any] | None = None
    for index, attempt in enumerate(attempts[: policy.max_attempts], start=1):
        status = attempt.get("status")
        timed_out = bool(attempt.get("timeout"))
        kind = classify(status, timed_out)
        valid, validation = validate_output(attempt.get("output"))
        event = {
            "attempt": index,
            "model": attempt.get("model", "unknown"),
            "status": status,
            "error_type": kind,
            "output_valid": valid,
            "validation": validation,
            "degraded": fallback_used,
        }
        events.append(event)
        if status == 200 and valid:
            final = {"ok": True, "reason": "validated", "degraded": fallback_used}
            break
        if kind == "auth_or_contract":
            final = {"ok": False, "reason": kind, "degraded": fallback_used}
            break
        if index == 1 and policy.allow_fallback and len(attempts) > 1:
            fallback_used = True
            continue
        final = {"ok": False, "reason": kind, "degraded": fallback_used}
        break
    if final is None:
        final = {"ok": False, "reason": "no_attempt", "degraded": False}
    return {
        "scenario": policy.scenario,
        "deadline_ms": policy.total_deadline_ms,
        "max_attempts": policy.max_attempts,
        "result": final,
        "events": events,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=sorted(POLICIES), default="support")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        cases = [
            [{"model": "claude", "status": 429}, {"model": "gpt", "status": 200, "output": {"answer": "ok"}}],
            [{"model": "claude", "status": 401}, {"model": "gpt", "status": 200, "output": {"answer": "must not bypass auth"}}],
            [{"model": "claude", "status": 200, "output": {"wrong": True}}],
        ]
        for case in cases:
            print(json.dumps(evaluate(POLICIES[args.scenario], case), ensure_ascii=False))
        return 0
    print(json.dumps(evaluate(POLICIES[args.scenario], []), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
