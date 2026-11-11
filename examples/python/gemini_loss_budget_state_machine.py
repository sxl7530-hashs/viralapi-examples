"""Network-free Gemini degradation policy for business loss budgets.

This example decides whether an application should validate, retry, fall back,
queue, reconcile, or stop. It deliberately does not call an API.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from enum import Enum


TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


class Decision(str, Enum):
    VALIDATE = "validate"
    RETRY = "retry"
    FALLBACK = "fallback"
    QUEUE = "queue"
    RECONCILE = "reconcile"
    STOP = "stop"


@dataclass(frozen=True)
class ScenarioPolicy:
    name: str
    total_deadline_ms: int
    validation_reserve_ms: int
    fallback_call_ms: int
    max_attempts: int
    allow_queue: bool
    allow_fallback: bool


@dataclass(frozen=True)
class Observation:
    status: int | None
    remaining_ms: int
    attempts: int = 1
    retry_after_ms: int = 0
    response_started: bool = False
    output_valid: bool | None = None
    breaker_open: bool = False
    fallback_compatible: bool = True
    read_only: bool = True


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    reason: str
    degraded: bool
    minimum_budget_ms: int


POLICIES = {
    "support": ScenarioPolicy(
        name="support",
        total_deadline_ms=8_000,
        validation_reserve_ms=700,
        fallback_call_ms=2_500,
        max_attempts=2,
        allow_queue=False,
        allow_fallback=True,
    ),
    "extraction": ScenarioPolicy(
        name="extraction",
        total_deadline_ms=30_000,
        validation_reserve_ms=2_000,
        fallback_call_ms=8_000,
        max_attempts=2,
        allow_queue=True,
        allow_fallback=True,
    ),
}


def decide(policy: ScenarioPolicy, obs: Observation) -> PolicyResult:
    """Classify one observation without performing retries or API calls."""
    if obs.remaining_ms < 0 or obs.attempts < 1 or obs.retry_after_ms < 0:
        raise ValueError("time budgets must be non-negative and attempts must be positive")

    if obs.response_started or (
        not obs.read_only and (obs.status != 200 or obs.output_valid is False)
    ):
        return PolicyResult(Decision.RECONCILE, "execution_outcome_unknown", True, 0)

    if obs.status == 200:
        if obs.output_valid is True:
            return PolicyResult(Decision.VALIDATE, "transport_and_contract_passed", False, 0)
        if obs.output_valid is False:
            required = policy.fallback_call_ms + policy.validation_reserve_ms
            if policy.allow_fallback and obs.fallback_compatible and obs.remaining_ms >= required:
                return PolicyResult(Decision.FALLBACK, "business_contract_failed", True, required)
            decision = Decision.QUEUE if policy.allow_queue else Decision.STOP
            return PolicyResult(decision, "invalid_output_without_safe_fallback", True, required)
        return PolicyResult(Decision.VALIDATE, "transport_passed_validation_required", False, 0)

    if obs.status in {400, 401, 403, 404, 422}:
        return PolicyResult(Decision.STOP, "fix_request_or_authorization", False, 0)

    if obs.status is None:
        return PolicyResult(Decision.RECONCILE, "timeout_or_disconnect_outcome_unknown", True, 0)

    if obs.status not in TRANSIENT_STATUS:
        return PolicyResult(Decision.STOP, "non_retryable_status", False, 0)

    retry_required = obs.retry_after_ms + policy.fallback_call_ms + policy.validation_reserve_ms
    if (
        not obs.breaker_open
        and obs.attempts < policy.max_attempts
        and obs.remaining_ms >= retry_required
    ):
        return PolicyResult(Decision.RETRY, "bounded_transient_retry", True, retry_required)

    fallback_required = policy.fallback_call_ms + policy.validation_reserve_ms
    if (
        policy.allow_fallback
        and obs.fallback_compatible
        and obs.remaining_ms >= fallback_required
    ):
        return PolicyResult(Decision.FALLBACK, "primary_unhealthy_or_retry_budget_exhausted", True, fallback_required)

    decision = Decision.QUEUE if policy.allow_queue else Decision.STOP
    return PolicyResult(decision, "insufficient_budget_for_safe_fallback", True, fallback_required)


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview a Gemini degradation decision")
    parser.add_argument("--scenario", choices=sorted(POLICIES), default="support")
    parser.add_argument("--status", default="429", help="HTTP status or 'timeout'")
    parser.add_argument("--remaining-ms", type=int, default=4_000)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--retry-after-ms", type=int, default=0)
    parser.add_argument("--breaker-open", action="store_true")
    parser.add_argument("--response-started", action="store_true")
    parser.add_argument("--invalid-output", action="store_true")
    args = parser.parse_args()

    status = None if args.status == "timeout" else int(args.status)
    output_valid = False if args.invalid_output else (True if status == 200 else None)
    observation = Observation(
        status=status,
        remaining_ms=args.remaining_ms,
        attempts=args.attempts,
        retry_after_ms=args.retry_after_ms,
        breaker_open=args.breaker_open,
        response_started=args.response_started,
        output_valid=output_valid,
    )
    result = decide(POLICIES[args.scenario], observation)
    print(json.dumps(asdict(result), ensure_ascii=False, default=lambda value: value.value))


if __name__ == "__main__":
    main()
