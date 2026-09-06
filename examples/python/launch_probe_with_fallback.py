#!/usr/bin/env python3
"""Probe ViralAPI with bounded timeout, retry, fallback, and structured logs."""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

LOG = logging.getLogger("viralapi.launch_probe")
RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Route:
    model: str
    cost_group: str
    timeout: float


ROUTES = (
    Route("gpt-4o-mini", "official_transfer", 8.0),
    Route("claude-sonnet-4", "stable_official", 8.0),
)


def run_probe() -> dict[str, Any]:
    request_id = str(uuid.uuid4())
    client = OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
        timeout=8.0,
        max_retries=0,
    )
    last_error: Exception | None = None
    for fallback_index, route in enumerate(ROUTES):
        started = time.monotonic()
        try:
            response = client.with_options(timeout=route.timeout).chat.completions.create(
                model=route.model,
                messages=[{"role": "user", "content": "Reply exactly: launch-probe-ok"}],
                max_tokens=16,
                extra_headers={
                    "X-Request-ID": request_id,
                    "X-Business-Scenario": "launch_probe",
                    "X-Cost-Group": route.cost_group,
                },
            )
            event = {
                "request_id": request_id,
                "model": route.model,
                "cost_group": route.cost_group,
                "fallback_index": fallback_index,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "status": "ok",
            }
            LOG.info("probe_success %s", json.dumps(event))
            return event
        except Exception as exc:  # SDK exceptions expose status_code when available.
            last_error = exc
            status = getattr(exc, "status_code", None)
            LOG.warning(
                "probe_error %s",
                json.dumps({
                    "request_id": request_id,
                    "model": route.model,
                    "fallback_index": fallback_index,
                    "status": status,
                    "error_type": type(exc).__name__,
                }),
            )
            if status not in RETRYABLE:
                break
            time.sleep(0.4)
    raise RuntimeError(f"probe_failed request_id={request_id}") from last_error


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_probe())
