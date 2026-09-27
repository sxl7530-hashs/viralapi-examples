"""No-network launch-evidence probe for an OpenAI-compatible API integration.

It models bounded retry/fallback decisions without making API calls or logging secrets.
Run: python3 examples/python/launch_evidence_probe.py
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    retryable: bool


def classify(status: int | None, *, idempotent: bool, remaining_ms: int) -> Decision:
    if status in (401, 403):
        return Decision("stop", "credentials_or_model_permission", False)
    if status in (400, 404, 422):
        return Decision("stop", "request_or_model_configuration", False)
    if status == 429:
        if remaining_ms < 800 or not idempotent:
            return Decision("queue_or_degrade", "rate_limit_deadline_or_side_effect", False)
        return Decision("bounded_retry", "temporary_rate_limit", True)
    if status in (408, 500, 502, 503, 504) or status is None:
        if not idempotent or remaining_ms < 800:
            return Decision("degrade", "transient_failure_without_safe_budget", False)
        return Decision("fallback_once", "transient_failure", True)
    if status == 200:
        return Decision("validate_business_result", "transport_success_is_not_business_success", False)
    return Decision("stop", "unexpected_status", False)


def main() -> None:
    cases = [
        (401, True, 5000),
        (429, True, 3000),
        (504, True, 3000),
        (504, False, 3000),
        (200, True, 3000),
    ]
    for status, idempotent, remaining_ms in cases:
        decision = classify(status, idempotent=idempotent, remaining_ms=remaining_ms)
        print({"status": status, "idempotent": idempotent, "remaining_ms": remaining_ms, **decision.__dict__})


if __name__ == "__main__":
    main()
