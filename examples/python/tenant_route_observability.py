#!/usr/bin/env python3
"""Tenant-aware ViralAPI routing example with observability fields.

This script is intentionally dependency-light. It can print a route plan without
network access, or call an OpenAI-compatible endpoint when VIRALAPI_API_KEY is set.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BASE_URL = os.environ.get("VIRALAPI_BASE_URL", "https://viralapi.ai/v1").rstrip("/")
API_KEY = os.environ.get("VIRALAPI_API_KEY", "")
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Candidate:
    model: str
    cost_group: str
    timeout_ms: int


ROUTES: dict[str, dict[str, Any]] = {
    "ai_support_reply": {
        "slo_ms": 8000,
        "max_attempts_per_candidate": 1,
        "candidates": [
            Candidate("claude-sonnet-4", "stable_official", 5000),
            Candidate("gpt-4.1-mini", "official_transfer", 4500),
            Candidate("gemini-2.5-flash", "welfare", 3500),
        ],
    },
    "bulk_content_generation": {
        "slo_ms": 60000,
        "max_attempts_per_candidate": 2,
        "candidates": [
            Candidate("gemini-2.5-flash", "welfare", 25000),
            Candidate("gpt-4.1-mini", "official_transfer", 25000),
        ],
    },
    "analyst_json_report": {
        "slo_ms": 30000,
        "max_attempts_per_candidate": 1,
        "candidates": [
            Candidate("claude-sonnet-4", "stable_official", 18000),
            Candidate("gemini-2.5-pro", "official_transfer", 18000),
        ],
    },
}


def load_routes(policy_path: str | None, tenant_id: str) -> dict[str, dict[str, Any]]:
    if not policy_path:
        return ROUTES
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("--policy requires PyYAML: python3 -m pip install pyyaml") from exc

    raw = yaml.safe_load(Path(policy_path).read_text())
    if not isinstance(raw, dict):
        raise RuntimeError(f"policy file is not a YAML object: {policy_path}")
    tenant = raw.get("tenants", {}).get(tenant_id, {})
    features = tenant.get("features", {}) if isinstance(tenant, dict) else {}
    routes: dict[str, dict[str, Any]] = {}
    for feature, cfg in features.items():
        candidates = [
            Candidate(
                model=str(item["model"]),
                cost_group=str(item["cost_group"]),
                timeout_ms=int(item.get("timeout_ms", 10000)),
            )
            for item in cfg.get("candidates", [])
        ]
        if candidates:
            routes[feature] = {
                "slo_ms": int(cfg.get("slo_ms", 30000)),
                "max_attempts_per_candidate": int(cfg.get("max_attempts_per_candidate", 1)),
                "candidates": candidates,
            }
    if not routes:
        known = ", ".join(sorted(raw.get("tenants", {}))) or "none"
        raise RuntimeError(f"no usable routes found for tenant_id={tenant_id!r} in {policy_path}; known tenants: {known}")
    return routes


def log_event(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, sort_keys=True))


def call_chat(candidate: Candidate, messages: list[dict[str, str]], request_id: str) -> dict[str, Any]:
    if not API_KEY:
        raise RuntimeError("VIRALAPI_API_KEY is not set")
    payload = json.dumps({"model": candidate.model, "messages": messages, "temperature": 0.2}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=max(candidate.timeout_ms / 1000, 1)) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        if exc.code in RETRYABLE_STATUS:
            raise RuntimeError(f"retryable_http_{exc.code}: {body}") from exc
        raise RuntimeError(f"fatal_http_{exc.code}: {body}") from exc


def gateway_chat(
    routes: dict[str, dict[str, Any]], tenant_id: str, feature: str, messages: list[dict[str, str]], dry_run: bool
) -> dict[str, Any]:
    route = routes[feature]
    request_id = f"{tenant_id}-{feature}-{uuid.uuid4().hex[:10]}"
    last_error: str | None = None

    for candidate_index, candidate in enumerate(route["candidates"]):
        for attempt in range(route["max_attempts_per_candidate"] + 1):
            started = time.time()
            fields = {
                "request_id": request_id,
                "tenant_id": tenant_id,
                "feature": feature,
                "route_name": feature,
                "model": candidate.model,
                "cost_group": candidate.cost_group,
                "attempt": attempt,
                "degraded": candidate_index > 0,
                "budget_bucket": "production" if candidate.cost_group == "stable_official" else "controlled",
            }
            try:
                if dry_run:
                    log_event("llm_route_candidate", **fields, timeout_ms=candidate.timeout_ms)
                    continue
                result = call_chat(candidate, messages, request_id)
                latency_ms = int((time.time() - started) * 1000)
                log_event("llm_call_success", **fields, latency_ms=latency_ms, final_status="ok")
                return result
            except Exception as exc:  # noqa: BLE001 - example surfaces every failed candidate.
                last_error = str(exc)
                latency_ms = int((time.time() - started) * 1000)
                log_event("llm_call_failed", **fields, latency_ms=latency_ms, error=last_error[:240])
                time.sleep(min(2**attempt, 4))

    if dry_run:
        return {"dry_run": True, "request_id": request_id, "candidates_checked": len(route["candidates"])}
    raise RuntimeError(f"all_models_failed request_id={request_id} last_error={last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="ViralAPI tenant route observability example")
    parser.add_argument("--policy", help="Optional route policy YAML file")
    parser.add_argument("--tenant-id", default="startup-basic")
    parser.add_argument("--feature", default="ai_support_reply")
    parser.add_argument("--prompt", default="Summarize this customer ticket and suggest a support reply.")
    parser.add_argument("--dry-run", action="store_true", help="Print route candidates without calling the API")
    args = parser.parse_args()

    routes = load_routes(args.policy, args.tenant_id)
    if args.feature not in routes:
        choices = ", ".join(sorted(routes))
        raise SystemExit(f"unknown --feature {args.feature!r}; choose one of: {choices}")
    messages = [{"role": "user", "content": args.prompt}]
    result = gateway_chat(routes, args.tenant_id, args.feature, messages, dry_run=args.dry_run)
    log_event("gateway_result", tenant_id=args.tenant_id, feature=args.feature, result=result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
