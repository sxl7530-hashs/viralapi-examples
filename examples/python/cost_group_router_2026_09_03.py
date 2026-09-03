"""Route LLM requests by business risk and cost group.

Dry run:
    python3 examples/python/cost_group_router_2026_09_03.py --scenario content_draft --dry-run

For real calls, set VIRALAPI_API_KEY and VIRALAPI_BASE_URL first.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

LOG = logging.getLogger("viralapi.cost_group_router")
RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Route:
    model: str
    cost_group: str
    timeout_seconds: float


ROUTES = {
    "customer_support": [
        Route("claude-sonnet-4", "stable_official", 12.0),
        Route("gpt-4o-mini", "official_transfer", 10.0),
    ],
    "content_draft": [
        Route("gemini-2.5-flash", "welfare", 25.0),
        Route("gpt-4o-mini", "official_transfer", 20.0),
    ],
    "data_analysis": [
        Route("gpt-4o-mini", "official_transfer", 30.0),
        Route("claude-sonnet-4", "stable_official", 25.0),
    ],
    "saas_feature": [
        Route("claude-sonnet-4", "stable_official", 10.0),
        Route("gpt-4o-mini", "stable_official", 10.0),
    ],
}


def route_plan(scenario: str, tenant_id: str, idempotent: bool) -> dict:
    routes = ROUTES.get(scenario, ROUTES["content_draft"])
    return {
        "request_id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "scenario": scenario,
        "idempotent": idempotent,
        "max_attempts_per_route": 2 if idempotent else 1,
        "routes": [asdict(route) for route in routes],
    }


def complete(*, scenario: str, tenant_id: str, idempotent: bool, dry_run: bool) -> str:
    plan = route_plan(scenario, tenant_id, idempotent)
    if dry_run:
        print(json.dumps({"event": "dry_run_route_plan", **plan}, ensure_ascii=False))
        return "dry-run"
    if OpenAI is None:
        raise RuntimeError("Install the openai package for real calls: pip install openai")

    client = OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.environ["VIRALAPI_BASE_URL"],
        timeout=15.0,
        max_retries=0,
    )
    messages = [{"role": "user", "content": "Summarize this ticket for routing."}]
    last_error = None
    for fallback_index, route_data in enumerate(plan["routes"]):
        route = Route(**route_data)
        for attempt in range(1, plan["max_attempts_per_route"] + 1):
            started = time.monotonic()
            try:
                response = client.with_options(timeout=route.timeout_seconds).chat.completions.create(
                    model=route.model,
                    messages=messages,
                    temperature=0.2,
                    extra_headers={
                        "X-Request-ID": plan["request_id"],
                        "X-Tenant-ID": tenant_id,
                        "X-Business-Scenario": scenario,
                        "X-Cost-Group": route.cost_group,
                    },
                )
                LOG.info(json.dumps({
                    "event": "llm_success", "request_id": plan["request_id"],
                    "model": route.model, "cost_group": route.cost_group,
                    "fallback_index": fallback_index, "attempt": attempt,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                }))
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                status = getattr(exc, "status_code", None)
                LOG.warning(json.dumps({
                    "event": "llm_error", "request_id": plan["request_id"],
                    "model": route.model, "cost_group": route.cost_group,
                    "attempt": attempt, "status": status,
                    "error_type": type(exc).__name__,
                }))
                if status not in RETRYABLE or attempt == plan["max_attempts_per_route"]:
                    break
                time.sleep(0.4 * 2 ** (attempt - 1))
    raise RuntimeError(f"all_routes_failed request_id={plan['request_id']}") from last_error


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=sorted(ROUTES), default="content_draft")
    parser.add_argument("--tenant-id", default="demo-tenant")
    parser.add_argument("--non-idempotent", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(complete(
        scenario=args.scenario,
        tenant_id=args.tenant_id,
        idempotent=not args.non_idempotent,
        dry_run=args.dry_run,
    ))
