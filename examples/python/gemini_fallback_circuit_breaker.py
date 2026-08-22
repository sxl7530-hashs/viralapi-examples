"""OpenAI-compatible Gemini fallback with bounded retries and a circuit breaker.

Dry run: python3 examples/python/gemini_fallback_circuit_breaker.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass

from openai import OpenAI

LOG = logging.getLogger("viralapi.router")
RETRYABLE = (408, 409, 425, 429, 500, 502, 503, 504)


@dataclass
class Candidate:
    model: str
    cost_group: str
    timeout: float


class Circuit:
    def __init__(self, threshold: int = 3, cooldown: float = 30.0) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures = 0
        self.opened_at = 0.0

    def available(self) -> bool:
        return not self.opened_at or time.monotonic() - self.opened_at >= self.cooldown

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic()

    def success(self) -> None:
        self.failures = 0
        self.opened_at = 0.0


def complete(messages: list[dict[str, str]], tenant_id: str, dry_run: bool = False) -> str:
    request_id = str(uuid.uuid4())
    candidates = [
        Candidate(os.getenv("PRIMARY_MODEL", "gemini-2.5-flash"), "stable_official", 8.0),
        Candidate(os.getenv("FALLBACK_MODEL", "gpt-4.1-mini"), "official_transfer", 8.0),
    ]
    circuits = {candidate.model: Circuit() for candidate in candidates}
    if dry_run:
        print(json.dumps({
            "request_id": request_id,
            "tenant_id": tenant_id,
            "route": [candidate.__dict__ for candidate in candidates],
            "policy": "retry=2, circuit_threshold=3, cooldown=30s",
        }))
        return "dry-run"

    client = OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
        timeout=8.0,
        max_retries=0,
    )
    last_error: Exception | None = None
    for index, candidate in enumerate(candidates):
        circuit = circuits[candidate.model]
        if not circuit.available():
            LOG.warning("route_skip request_id=%s model=%s reason=circuit_open", request_id, candidate.model)
            continue
        for attempt in range(1, 3):
            started = time.monotonic()
            try:
                result = client.with_options(timeout=candidate.timeout).chat.completions.create(
                    model=candidate.model,
                    messages=messages,
                    temperature=0.2,
                    extra_headers={"X-Request-ID": request_id, "X-Tenant-ID": tenant_id},
                )
                circuit.success()
                LOG.info(
                    "route_success request_id=%s tenant_id=%s model=%s cost_group=%s attempt=%s degraded=%s latency_ms=%s",
                    request_id,
                    tenant_id,
                    candidate.model,
                    candidate.cost_group,
                    attempt,
                    index > 0,
                    round((time.monotonic() - started) * 1000),
                )
                return result.choices[0].message.content or ""
            except Exception as exc:  # SDK exception classes vary by version.
                last_error = exc
                status = getattr(exc, "status_code", None)
                circuit.failure()
                LOG.warning(
                    "route_error request_id=%s tenant_id=%s model=%s attempt=%s status=%s error=%s",
                    request_id,
                    tenant_id,
                    candidate.model,
                    attempt,
                    status,
                    type(exc).__name__,
                )
                if status not in RETRYABLE or attempt == 2:
                    break
                time.sleep(0.5 * (2 ** (attempt - 1)))
    raise RuntimeError(f"all fallback candidates failed request_id={request_id}") from last_error


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", default="startup-basic")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(complete([{"role": "user", "content": "Classify this support ticket."}], args.tenant_id, args.dry_run))
