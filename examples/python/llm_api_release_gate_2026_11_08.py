#!/usr/bin/env python3
"""Offline release gate for LLM API launch checks."""
from dataclasses import dataclass
from enum import Enum


class Decision(str, Enum):
    RELEASE = "release"
    RETRY = "retry"
    FALLBACK = "fallback"
    MANUAL = "manual"


@dataclass(frozen=True)
class Result:
    decision: Decision
    reason: str


def classify(*, status: int | None, timed_out: bool, idempotent: bool,
             deadline_left_ms: int, validation_passed: bool,
             fallback_tested: bool) -> Result:
    if timed_out:
        return Result(Decision.MANUAL, "unknown_outcome_reconcile_by_operation_id")
    if status in (400, 401, 403):
        return Result(Decision.MANUAL, "fix_contract_or_authorization")
    if status == 429 or status in (502, 503, 504):
        if idempotent and deadline_left_ms > 500 and fallback_tested:
            return Result(Decision.FALLBACK, "one_bounded_alternate_attempt")
        if idempotent and deadline_left_ms > 500:
            return Result(Decision.RETRY, "one_bounded_retry")
        return Result(Decision.MANUAL, "no_safe_budget_for_retry")
    if status == 200 and not validation_passed:
        return Result(Decision.MANUAL, "business_validation_failed")
    if status == 200:
        return Result(Decision.RELEASE, "transport_and_business_checks_passed")
    return Result(Decision.MANUAL, "unclassified_failure")


if __name__ == "__main__":
    print(classify(status=429, timed_out=False, idempotent=True,
                   deadline_left_ms=2000, validation_passed=False,
                   fallback_tested=True))
