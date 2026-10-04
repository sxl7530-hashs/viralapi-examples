#!/usr/bin/env python3
"""Offline LLM API launch troubleshooting decision tree.

No network calls and no credential reads. This is a production-readiness
policy drill for ViralAPI/OpenAI-compatible integrations.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

Action = Literal["stop", "retry_once", "fallback_once", "lookup_state", "queue_review"]


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str
    retry_allowed: bool
    fallback_allowed: bool
    requires_state_lookup: bool
    log_level: str


def route_failure(status: int, remaining_ms: int, idempotent: bool, fallback_used: bool) -> dict[str, object]:
    """Classify a failed LLM API attempt before retrying or falling back."""
    if status in {400, 401, 403, 404, 422}:
        return asdict(Decision("stop", "config_or_contract_error", False, False, False, "error"))
    if remaining_ms < 1_200:
        return asdict(Decision("queue_review", "deadline_exhausted", False, False, False, "warn"))
    if not idempotent:
        return asdict(Decision("lookup_state", "side_effect_status_unknown", False, False, True, "warn"))
    if status == 429:
        return asdict(Decision("retry_once", "rate_limited_with_budget", True, False, False, "warn"))
    if status in {408, 500, 502, 503, 504} and not fallback_used:
        return asdict(Decision("fallback_once", "transient_upstream_failure", False, True, False, "warn"))
    return asdict(Decision("queue_review", "no_safe_automatic_recovery", False, False, False, "warn"))


def main() -> None:
    cases = [
        {"status": 401, "remaining_ms": 5_000, "idempotent": True, "fallback_used": False},
        {"status": 429, "remaining_ms": 3_000, "idempotent": True, "fallback_used": False},
        {"status": 504, "remaining_ms": 3_500, "idempotent": True, "fallback_used": False},
        {"status": 504, "remaining_ms": 3_500, "idempotent": True, "fallback_used": True},
        {"status": 504, "remaining_ms": 3_500, "idempotent": False, "fallback_used": False},
    ]
    for case in cases:
        print({"input": case, "decision": route_failure(**case)})


if __name__ == "__main__":
    main()
