#!/usr/bin/env python3
"""Build a deterministic, secret-free LLM launch evidence report."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Iterable

REQUIRED_TESTS = {"auth_401", "rate_limit_429", "side_effect_timeout", "transient_503"}


def evaluate_release(results: Iterable[dict], config: dict | None = None) -> dict:
    rows = list(results)
    names = {str(row.get("name")) for row in rows}
    missing = sorted(REQUIRED_TESTS - names)
    failed = [row["name"] for row in rows if row.get("observed") != row.get("expected")]
    safe_config = config or {
        "timeout_ms": 12000,
        "max_attempts": 2,
        "fallback_limit": 1,
        "retry_owner": "gateway",
    }
    encoded = json.dumps(safe_config, sort_keys=True, separators=(",", ":")).encode()
    return {
        "schema_version": 1,
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "config_hash": hashlib.sha256(encoded).hexdigest()[:16],
        "decision": "go" if not missing and not failed else "no_go",
        "missing_tests": missing,
        "failed_tests": failed,
        "tests": rows,
    }


if __name__ == "__main__":
    sample = [
        {"name": "auth_401", "observed": "stop", "expected": "stop"},
        {"name": "rate_limit_429", "observed": "retry_once", "expected": "retry_once"},
        {"name": "side_effect_timeout", "observed": "lookup_state", "expected": "lookup_state"},
        {"name": "transient_503", "observed": "fallback_once", "expected": "fallback_once"},
    ]
    print(json.dumps(evaluate_release(sample), ensure_ascii=False, indent=2))
