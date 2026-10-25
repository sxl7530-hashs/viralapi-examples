#!/usr/bin/env python3
"""Offline launch checklist decision helper for OpenAI-compatible LLM APIs."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    retryable: bool
    fallback_allowed: bool
    state_lookup_required: bool
    log_level: str


def decide(status: int, remaining_ms: int, idempotent: bool, fallback_used: bool) -> dict[str, object]:
    if status in {400, 401, 403, 404, 422}:
        return asdict(Decision("stop", "configuration_or_contract_error", False, False, False, "error"))
    if remaining_ms < 1200:
        return asdict(Decision("queue_review", "deadline_exhausted", False, False, False, "warn"))
    if not idempotent:
        return asdict(Decision("lookup_state", "side_effect_status_unknown", False, False, True, "warn"))
    if status == 429:
        return asdict(Decision("retry_once", "rate_limited_with_retry_budget", True, False, False, "warn"))
    if status in {408, 500, 502, 503, 504} and not fallback_used:
        return asdict(Decision("fallback_once", "transient_upstream_failure", False, True, False, "warn"))
    return asdict(Decision("queue_review", "no_safe_automatic_recovery", False, False, False, "warn"))


def self_test() -> None:
    assert decide(401, 5000, True, False)["action"] == "stop"
    assert decide(429, 3000, True, False)["action"] == "retry_once"
    assert decide(504, 3000, True, False)["action"] == "fallback_once"
    assert decide(504, 3000, True, True)["action"] == "queue_review"
    assert decide(504, 3000, False, False)["state_lookup_required"] is True
    assert decide(504, 900, True, False)["action"] == "queue_review"
    print("self-test: ok")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--scenario", default="support")
    parser.add_argument("--status", type=int, default=429)
    parser.add_argument("--remaining-ms", type=int, default=3000)
    parser.add_argument("--non-idempotent", action="store_true")
    parser.add_argument("--fallback-used", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    print(json.dumps({"scenario": args.scenario, "decision": decide(args.status, args.remaining_ms, not args.non_idempotent, args.fallback_used)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
