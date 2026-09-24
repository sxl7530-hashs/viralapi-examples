"""Preview a scenario-aware LLM cost admission policy without network access.

Example:
  python3 examples/python/cost_admission_routing.py \
    --scenario content_batch --spent 920 --budget 1000 --dry-run

Model IDs and groups are examples. Use values enabled for the ViralAPI account.
"""
from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Route:
    model: str
    cost_group: str
    timeout_s: int
    max_attempts: int


POLICIES = {
    "support_reply": [
        Route("claude-sonnet-4", "stable_official", 8, 2),
        Route("gpt-4.1-mini", "official_transfer", 5, 1),
    ],
    "content_batch": [
        Route("gemini-2.5-flash", "welfare", 20, 2),
        Route("gpt-4.1-mini", "official_transfer", 12, 1),
    ],
    "analytics": [
        Route("gpt-4.1-mini", "official_transfer", 20, 2),
        Route("claude-sonnet-4", "stable_official", 12, 1),
    ],
    "paid_saas": [Route("claude-sonnet-4", "stable_official", 8, 2)],
}


def admit(scenario: str, spent: float, budget: float) -> tuple[str, list[Route]]:
    if budget <= 0:
        raise ValueError("budget_must_be_positive")
    ratio = spent / budget
    if ratio >= 1:
        return "reject:budget_exhausted", []
    if ratio >= 0.9 and scenario == "content_batch":
        return "queue:next_budget_window", []
    if ratio >= 0.9:
        return "allow:core_path_only", POLICIES[scenario]
    return "allow", POLICIES[scenario]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=sorted(POLICIES), required=True)
    parser.add_argument("--spent", type=float, required=True)
    parser.add_argument("--budget", type=float, required=True)
    parser.add_argument("--tenant-id", default="tenant-demo")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    decision, routes = admit(args.scenario, args.spent, args.budget)
    event = {
        "event": "cost_admission_decision",
        "request_id": str(uuid.uuid4()),
        "tenant_id": args.tenant_id,
        "scenario": args.scenario,
        "budget_used_ratio": round(args.spent / args.budget, 4),
        "decision": decision,
        "routes": [asdict(route) for route in routes],
        "retry_owner": "application_router",
        "sdk_max_retries": 0,
    }
    print(json.dumps(event, ensure_ascii=False, sort_keys=True))
    if not args.dry_run:
        print("Preview only: integrate the selected route with your account-configured endpoint.")


if __name__ == "__main__":
    main()