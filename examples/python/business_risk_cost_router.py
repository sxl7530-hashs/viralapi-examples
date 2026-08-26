"""Business-risk based LLM API cost router for ViralAPI.

Dry run:
    python3 examples/python/business_risk_cost_router.py --scenario customer_support --tenant-id demo --dry-run

Runtime requirements for real calls:
    export VIRALAPI_API_KEY=...
    export VIRALAPI_BASE_URL=https://viralapi.ai/v1
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
except ImportError:  # Keep dry-run usable in minimal environments.
    OpenAI = None  # type: ignore[assignment]

LOG = logging.getLogger("viralapi.cost_router")
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class RouteCandidate:
    model: str
    cost_group: str
    timeout_seconds: float


ROUTES: dict[str, list[RouteCandidate]] = {
    "customer_support": [
        RouteCandidate("claude-sonnet-4", "stable_official", 12.0),
        RouteCandidate("gpt-4o-mini", "official_transfer", 10.0),
    ],
    "content_draft": [
        RouteCandidate("gemini-2.5-flash", "welfare", 25.0),
        RouteCandidate("gpt-4o-mini", "official_transfer", 20.0),
    ],
    "data_analysis": [
        RouteCandidate("gpt-4o-mini", "official_transfer", 30.0),
        RouteCandidate("claude-sonnet-4", "stable_official", 25.0),
    ],
    "saas_feature": [
        RouteCandidate("claude-sonnet-4", "stable_official", 10.0),
        RouteCandidate("gpt-4o-mini", "stable_official", 10.0),
    ],
}


def route_plan(scenario: str, tenant_id: str, idempotent: bool) -> dict:
    candidates = ROUTES.get(scenario, ROUTES["content_draft"])
    return {
        "request_id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "scenario": scenario,
        "idempotent": idempotent,
        "max_attempts_per_candidate": 2 if idempotent else 1,
        "candidates": [asdict(candidate) for candidate in candidates],
    }


def complete(*, scenario: str, tenant_id: str, messages: list[dict[str, str]], idempotent: bool, dry_run: bool) -> str:
    plan = route_plan(scenario, tenant_id, idempotent)
    if dry_run:
        print(json.dumps({"event": "dry_run_route_plan", **plan}, ensure_ascii=False))
        return "dry-run"

    if OpenAI is None:
        raise RuntimeError("Install the openai package for real calls: pip install openai")

    client = OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
        timeout=15.0,
        max_retries=0,
    )
    last_error: Exception | None = None
    for fallback_index, candidate_data in enumerate(plan["candidates"]):
        candidate = RouteCandidate(**candidate_data)
        for attempt in range(1, plan["max_attempts_per_candidate"] + 1):
            started = time.monotonic()
            try:
                response = client.with_options(timeout=candidate.timeout_seconds).chat.completions.create(
                    model=candidate.model,
                    messages=messages,
                    temperature=0.2,
                    extra_headers={
                        "X-Request-ID": plan["request_id"],
                        "X-Tenant-ID": tenant_id,
                        "X-Business-Scenario": scenario,
                        "X-Cost-Group": candidate.cost_group,
                    },
                )
                LOG.info("llm_success %s", json.dumps({
                    "request_id": plan["request_id"],
                    "tenant_id": tenant_id,
                    "scenario": scenario,
                    "model": candidate.model,
                    "cost_group": candidate.cost_group,
                    "fallback_index": fallback_index,
                    "attempt": attempt,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                }))
                return response.choices[0].message.content or ""
            except Exception as exc:  # SDK exception classes vary by version.
                last_error = exc
                status = getattr(exc, "status_code", None)
                LOG.warning("llm_error %s", json.dumps({
                    "request_id": plan["request_id"],
                    "tenant_id": tenant_id,
                    "scenario": scenario,
                    "model": candidate.model,
                    "cost_group": candidate.cost_group,
                    "attempt": attempt,
                    "status": status,
                    "error_type": type(exc).__name__,
                }))
                if status not in RETRYABLE_STATUS or attempt >= plan["max_attempts_per_candidate"]:
                    break
                time.sleep(0.4 * (2 ** (attempt - 1)))
    raise RuntimeError(f"all_routes_failed request_id={plan['request_id']} scenario={scenario}") from last_error


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="customer_support", choices=sorted(ROUTES))
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
        messages=[{"role": "user", "content": "Summarize this customer ticket for routing."}],
    ))
