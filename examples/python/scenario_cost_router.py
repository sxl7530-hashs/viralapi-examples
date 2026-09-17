"""Scenario-aware cost routing for an OpenAI-compatible LLM gateway.

Preview without credentials:
  python3 examples/python/scenario_cost_router.py --scenario content_batch --dry-run

Real call:
  pip install openai
  export VIRALAPI_API_KEY="***"
  export VIRALAPI_BASE_URL="https://your-endpoint/v1"
  python3 examples/python/scenario_cost_router.py --scenario support_reply

Model IDs and available groups depend on your ViralAPI account configuration.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
import uuid
from dataclasses import asdict, dataclass

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

LOG = logging.getLogger("viralapi.scenario_cost_router")
RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Route:
    model: str
    cost_group: str
    timeout_s: float
    max_attempts: int


POLICIES = {
    "support_reply": [
        Route(os.getenv("STABLE_MODEL", "claude-sonnet-4"), "stable_official", 8, 2),
        Route(os.getenv("TRANSFER_MODEL", "gpt-4.1-mini"), "official_transfer", 5, 1),
    ],
    "content_batch": [
        Route(os.getenv("WELFARE_MODEL", "gemini-2.5-flash"), "welfare", 20, 2),
        Route(os.getenv("TRANSFER_MODEL", "gpt-4.1-mini"), "official_transfer", 12, 1),
    ],
    "analytics": [
        Route(os.getenv("TRANSFER_MODEL", "gpt-4.1-mini"), "official_transfer", 20, 2),
        Route(os.getenv("STABLE_MODEL", "claude-sonnet-4"), "stable_official", 12, 1),
    ],
    "paid_saas": [
        Route(os.getenv("STABLE_MODEL", "claude-sonnet-4"), "stable_official", 7, 2),
    ],
}


def select_routes(scenario: str, spent: float, budget: float) -> list[Route]:
    if budget <= 0:
        raise ValueError("budget_must_be_positive")
    ratio = spent / budget
    if ratio >= 1:
        raise RuntimeError("monthly_budget_exhausted")
    if ratio >= 0.9 and scenario == "content_batch":
        raise RuntimeError("queue_for_next_budget_window")
    return list(POLICIES[scenario])


def event(**fields) -> None:
    LOG.info(json.dumps(fields, ensure_ascii=False, sort_keys=True))


def complete(*, scenario: str, tenant_id: str, spent: float, budget: float,
             messages: list[dict], deadline_s: float, dry_run: bool) -> str:
    request_id = str(uuid.uuid4())
    routes = select_routes(scenario, spent, budget)
    if dry_run:
        print(json.dumps({
            "event": "route_plan",
            "request_id": request_id,
            "tenant_id": tenant_id,
            "scenario": scenario,
            "budget_used_ratio": round(spent / budget, 4),
            "deadline_s": deadline_s,
            "routes": [asdict(route) for route in routes],
        }, ensure_ascii=False))
        return "dry-run"
    if OpenAI is None:
        raise RuntimeError("Install dependency: pip install openai")

    client = OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.environ["VIRALAPI_BASE_URL"],
        max_retries=0,  # Keep retry ownership in one layer.
    )
    deadline = time.monotonic() + deadline_s
    last_error = None
    for fallback_index, route in enumerate(routes):
        for attempt in range(1, route.max_attempts + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0.5:
                raise TimeoutError(f"total_deadline_exceeded request_id={request_id}")
            started = time.monotonic()
            try:
                response = client.with_options(timeout=min(route.timeout_s, remaining)).chat.completions.create(
                    model=route.model,
                    messages=messages,
                    temperature=0.2,
                    extra_headers={
                        "X-Request-ID": request_id,
                        "X-Tenant-ID": tenant_id,
                        "X-Cost-Group": route.cost_group,
                    },
                )
                usage = getattr(response, "usage", None)
                event(event="llm_success", request_id=request_id, tenant_id=tenant_id,
                      scenario=scenario, model=route.model, cost_group=route.cost_group,
                      fallback_index=fallback_index, attempt=attempt,
                      latency_ms=round((time.monotonic() - started) * 1000),
                      prompt_tokens=getattr(usage, "prompt_tokens", None),
                      completion_tokens=getattr(usage, "completion_tokens", None))
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                status = getattr(exc, "status_code", None)
                event(event="llm_error", request_id=request_id, tenant_id=tenant_id,
                      scenario=scenario, model=route.model, cost_group=route.cost_group,
                      fallback_index=fallback_index, attempt=attempt, status=status,
                      error_type=type(exc).__name__)
                if status not in RETRYABLE or attempt == route.max_attempts:
                    break
                delay = min(2.0, 0.3 * (2 ** (attempt - 1)) + random.random() * 0.2)
                time.sleep(min(delay, max(0, deadline - time.monotonic())))
    raise RuntimeError(f"all_routes_failed request_id={request_id}") from last_error


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=sorted(POLICIES), default="content_batch")
    parser.add_argument("--tenant-id", default="demo-tenant")
    parser.add_argument("--spent", type=float, default=720)
    parser.add_argument("--budget", type=float, default=1000)
    parser.add_argument("--deadline", type=float, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = complete(
        scenario=args.scenario,
        tenant_id=args.tenant_id,
        spent=args.spent,
        budget=args.budget,
        messages=[{"role": "user", "content": "Summarize this customer ticket."}],
        deadline_s=args.deadline,
        dry_run=args.dry_run,
    )
    print(result)
