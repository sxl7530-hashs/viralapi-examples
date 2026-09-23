#!/usr/bin/env python3
"""Exercise Gemini-first fallback decisions without making an API request.

Run:
    python3 examples/python/gemini_fallback_failure_drill.py
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json


@dataclass(frozen=True)
class Decision:
    scenario: str
    failure: str
    total_deadline_ms: int
    primary_action: str
    fallback_action: str
    terminal_action: str
    cost_group: str
    log_fields: list[str]


POLICY = [
    Decision(
        scenario="ai_support",
        failure="Gemini 429 after 900 ms",
        total_deadline_ms=8000,
        primary_action="retry once with full jitter only when 3,000 ms remain",
        fallback_action="use a regression-tested GPT or Claude route",
        terminal_action="return a controlled handoff response",
        cost_group="stable_official",
        log_fields=["request_id", "tenant_id", "model", "status_code", "degraded"],
    ),
    Decision(
        scenario="batch_content",
        failure="Gemini 503 after worker timeout",
        total_deadline_ms=60000,
        primary_action="retry once; respect queue lease and idempotency key",
        fallback_action="route to an approved alternate model",
        terminal_action="retry asynchronously, then move to the dead-letter queue",
        cost_group="welfare",
        log_fields=["job_id", "tenant_id", "attempt", "cost_group", "retryable"],
    ),
    Decision(
        scenario="contract_extraction",
        failure="fallback returned invalid JSON",
        total_deadline_ms=35000,
        primary_action="do not retry the same invalid output",
        fallback_action="validate the next approved route against the same schema",
        terminal_action="hold for review; never write unvalidated fields",
        cost_group="official_transfer",
        log_fields=["request_id", "schema_version", "validation_error", "model", "degraded"],
    ),
]


def main() -> None:
    print(json.dumps([asdict(decision) for decision in POLICY], indent=2))


if __name__ == "__main__":
    main()