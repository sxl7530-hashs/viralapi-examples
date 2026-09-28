#!/usr/bin/env python3
"""No-network LLM launch eligibility policy drill.

This script makes no API calls and never reads credentials. It demonstrates
bounded retry, one validated fallback, and the boundary before side effects.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


Decision = Literal["stop_and_fix", "retry_once", "fallback_once", "manual_or_queue"]


@dataclass(frozen=True)
class GateResult:
    decision: Decision
    reason: str
    retry_allowed: bool
    fallback_allowed: bool
    requires_state_lookup: bool


def decide(status: int, remaining_ms: int, idempotent: bool, fallback_used: bool) -> dict[str, object]:
    """Return a deterministic action without sending a model request."""
    if status in {401, 403, 400, 404, 422}:
        return asdict(GateResult("stop_and_fix", "configuration_or_contract", False, False, False))
    if not idempotent:
        return asdict(GateResult("manual_or_queue", "side_effect_requires_state_lookup", False, False, True))
    if remaining_ms < 1_000:
        return asdict(GateResult("manual_or_queue", "deadline_exhausted", False, False, False))
    if status == 429:
        return asdict(GateResult("retry_once", "transient_rate_limit", True, False, False))
    if status in {408, 500, 502, 503, 504}:
        if fallback_used:
            return asdict(GateResult("manual_or_queue", "fallback_already_used", False, False, False))
        return asdict(GateResult("fallback_once", "transient_upstream_failure", False, True, False))
    return asdict(GateResult("manual_or_queue", "business_validation_or_unknown_failure", False, False, False))


if __name__ == "__main__":
    cases = [
        (401, 5_000, True, False),
        (429, 2_400, True, False),
        (504, 3_000, True, False),
        (504, 3_000, True, True),
        (504, 3_000, False, False),
    ]
    for case in cases:
        print({"input": case, "result": decide(*case)})