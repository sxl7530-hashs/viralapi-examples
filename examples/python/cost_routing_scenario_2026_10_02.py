"""Offline cost-routing policy preview for ViralAPI.

This example performs no network calls. It demonstrates admission control,
scenario-based group selection, bounded fallback, and safe event fields.
"""
from __future__ import annotations

from dataclasses import dataclass

GROUP_DISCOUNT = {"welfare": 0.15, "official_transfer": 0.60, "stable_official": 0.80}

POLICIES = {
    "support_reply": ("stable_official", "official_transfer", True),
    "saas_report": ("stable_official", "official_transfer", True),
    "content_batch": ("welfare", "official_transfer", False),
    "internal_analytics": ("official_transfer", "welfare", False),
}


@dataclass(frozen=True)
class Budget:
    limit_usd: float
    used_usd: float
    core_reserve_usd: float = 20.0


def admit(scenario: str, budget: Budget) -> tuple[bool, str]:
    primary, _fallback, customer_visible = POLICIES[scenario]
    ratio = budget.used_usd / budget.limit_usd
    if ratio >= 1:
        return False, "monthly_budget_exhausted"
    if ratio >= 0.90 and not customer_visible:
        return False, "pause_low_priority_batch"
    if customer_visible and budget.limit_usd - budget.used_usd < budget.core_reserve_usd:
        return False, "core_reserve_too_low"
    return True, primary


def classify(status: int) -> str:
    if status in {401, 403}:
        return "authorization_or_model_access"
    if status in {400, 404, 422}:
        return "request_contract"
    if status == 429:
        return "rate_or_budget_limit"
    if status in {408, 500, 502, 503, 504}:
        return "transient_upstream"
    return "success_or_unknown"


def preview(scenario: str, budget: Budget, first_status: int) -> list[dict]:
    admitted, route = admit(scenario, budget)
    primary, fallback, _customer_visible = POLICIES[scenario]
    if not admitted:
        return [{"scenario": scenario, "admitted": False, "reason": route}]

    error_class = classify(first_status)
    events = [{
        "scenario": scenario,
        "attempt": 1,
        "model_group": primary,
        "discount_vs_official": GROUP_DISCOUNT[primary],
        "status_code": first_status,
        "error_class": error_class,
        "fallback_used": False,
    }]
    if error_class == "transient_upstream":
        events.append({
            "scenario": scenario,
            "attempt": 2,
            "model_group": fallback,
            "discount_vs_official": GROUP_DISCOUNT[fallback],
            "status_code": 200,
            "error_class": "success",
            "fallback_used": True,
        })
    return events


if __name__ == "__main__":
    for event in preview("support_reply", Budget(1000, 760), 504):
        print(event)
