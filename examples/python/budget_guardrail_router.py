"""Budget-aware LLM routing for small teams using an OpenAI-compatible API.

Dry run (no dependency or credentials required):
  python3 examples/python/budget_guardrail_router.py --scenario content_batch --dry-run

Real call:
  pip install openai
  export VIRALAPI_API_KEY="..."
  export VIRALAPI_BASE_URL="https://your-endpoint/v1"
  python3 examples/python/budget_guardrail_router.py --scenario support_reply

Model IDs and group availability depend on your ViralAPI account configuration.
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

LOG = logging.getLogger("viralapi.budget_router")
RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Route:
    model: str
    group: str
    timeout_s: float
    estimated_cost_per_1k_tokens: float


POLICIES = {
    "support_reply": [
        Route(os.getenv("STABLE_MODEL", "claude-sonnet-4"), "stable_official", 10, 0.80),
        Route(os.getenv("TRANSFER_MODEL", "gpt-4o-mini"), "official_transfer", 8, 0.60),
    ],
    "content_batch": [
        Route(os.getenv("WELFARE_MODEL", "gemini-2.5-flash"), "welfare", 25, 0.15),
        Route(os.getenv("TRANSFER_MODEL", "gpt-4o-mini"), "official_transfer", 18, 0.60),
    ],
    "analytics": [
        Route(os.getenv("TRANSFER_MODEL", "gpt-4o-mini"), "official_transfer", 30, 0.60),
        Route(os.getenv("STABLE_MODEL", "claude-sonnet-4"), "stable_official", 20, 0.80),
    ],
    "paid_saas": [
        Route(os.getenv("STABLE_MODEL", "claude-sonnet-4"), "stable_official", 9, 0.80),
    ],
}


def estimate_tokens(messages: list[dict]) -> int:
    # Conservative planning estimate; use provider usage fields for settlement.
    chars = sum(len(str(m.get("content", ""))) for m in messages)
    return max(256, chars // 3 + 600)


def select_routes(scenario: str, spent: float, monthly_budget: float) -> list[Route]:
    routes = list(POLICIES[scenario])
    ratio = spent / monthly_budget if monthly_budget > 0 else 1.0
    if ratio >= 1.0:
        raise RuntimeError("monthly_budget_exhausted")
    # Protect customer-facing routes; postpone low-risk batch work after 90%.
    if ratio >= 0.90 and scenario == "content_batch":
        raise RuntimeError("budget_guardrail_queue_for_next_window")
    return routes


def log_event(**fields) -> None:
    LOG.info(json.dumps(fields, ensure_ascii=False, sort_keys=True))


def complete(*, scenario: str, tenant_id: str, spent: float, budget: float,
             messages: list[dict], dry_run: bool) -> str:
    request_id = str(uuid.uuid4())
    routes = select_routes(scenario, spent, budget)
    estimated_tokens = estimate_tokens(messages)
    plan = {
        "request_id": request_id,
        "tenant_id": tenant_id,
        "scenario": scenario,
        "budget_used_ratio": round(spent / budget, 4),
        "estimated_tokens": estimated_tokens,
        "routes": [asdict(r) for r in routes],
    }
    if dry_run:
        print(json.dumps({"event": "route_plan", **plan}, ensure_ascii=False))
        return "dry-run"
    if OpenAI is None:
        raise RuntimeError("Install dependency: pip install openai")

    client = OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.environ["VIRALAPI_BASE_URL"],
        max_retries=0,
        timeout=15,
    )
    deadline = time.monotonic() + 35
    last_error = None
    for fallback_index, route in enumerate(routes):
        for attempt in range(1, 3):
            remaining = deadline - time.monotonic()
            if remaining <= 1:
                raise TimeoutError(f"total_deadline_exceeded request_id={request_id}")
            started = time.monotonic()
            try:
                response = client.with_options(timeout=min(route.timeout_s, remaining)).chat.completions.create(
                    model=route.model,
                    messages=messages,
                    temperature=0.2,
                    extra_headers={"X-Request-ID": request_id, "X-Tenant-ID": tenant_id},
                )
                usage = getattr(response, "usage", None)
                log_event(event="llm_success", request_id=request_id, tenant_id=tenant_id,
                          scenario=scenario, model=route.model, cost_group=route.group,
                          attempt=attempt, fallback_index=fallback_index,
                          latency_ms=round((time.monotonic() - started) * 1000),
                          prompt_tokens=getattr(usage, "prompt_tokens", None),
                          completion_tokens=getattr(usage, "completion_tokens", None))
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                status = getattr(exc, "status_code", None)
                log_event(event="llm_error", request_id=request_id, tenant_id=tenant_id,
                          scenario=scenario, model=route.model, cost_group=route.group,
                          attempt=attempt, fallback_index=fallback_index, status=status,
                          error_type=type(exc).__name__)
                if status not in RETRYABLE or attempt == 2:
                    break
                retry_after = min(2.0, 0.35 * (2 ** (attempt - 1)) + random.random() * 0.15)
                time.sleep(min(retry_after, max(0, deadline - time.monotonic())))
    raise RuntimeError(f"all_routes_failed request_id={request_id}") from last_error


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=sorted(POLICIES), default="content_batch")
    parser.add_argument("--tenant-id", default="demo-tenant")
    parser.add_argument("--spent", type=float, default=720)
    parser.add_argument("--budget", type=float, default=1000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = complete(scenario=args.scenario, tenant_id=args.tenant_id,
                      spent=args.spent, budget=args.budget,
                      messages=[{"role": "user", "content": "Summarize this support ticket."}],
                      dry_run=args.dry_run)
    print(result)
