#!/usr/bin/env python3
"""Offline launch gate for LLM API failures and release decisions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str


def classify(
    *,
    status: int | None,
    timed_out: bool,
    idempotent: bool,
    deadline_left_ms: int,
    validation_passed: bool,
    fallback_tested: bool,
) -> Decision:
    """Map one observed result to a bounded next action without network calls."""
    if timed_out:
        return Decision("manual", "unknown_outcome_reconcile_by_operation_id")
    if status in (400, 401, 403):
        return Decision("manual", "fix_request_or_authorization")
    if status in (429, 502, 503, 504):
        if not idempotent or deadline_left_ms <= 500:
            return Decision("manual", "no_safe_retry_budget")
        if fallback_tested:
            return Decision("fallback", "one_bounded_validated_alternate")
        return Decision("retry", "one_bounded_retry")
    if status == 200 and not validation_passed:
        return Decision("manual", "business_validation_failed")
    if status == 200:
        return Decision("release", "transport_and_business_checks_passed")
    return Decision("manual", "unclassified_failure")


def main() -> None:
    cases = {
        "rate_limited_with_tested_fallback": classify(
            status=429,
            timed_out=False,
            idempotent=True,
            deadline_left_ms=1800,
            validation_passed=False,
            fallback_tested=True,
        ),
        "read_timeout": classify(
            status=None,
            timed_out=True,
            idempotent=True,
            deadline_left_ms=1800,
            validation_passed=False,
            fallback_tested=True,
        ),
        "valid_response": classify(
            status=200,
            timed_out=False,
            idempotent=False,
            deadline_left_ms=0,
            validation_passed=True,
            fallback_tested=False,
        ),
    }
    for name, decision in cases.items():
        print(f"{name}: {decision.action} ({decision.reason})")


if __name__ == "__main__":
    main()