"""Small production-oriented OpenAI-compatible client for ViralAPI.

Dry run: python3 examples/python/sdk_production_client.py --dry-run
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

LOG = logging.getLogger("viralapi.sdk")
RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Settings:
    base_url: str
    model: str
    timeout: float
    max_attempts: int
    cost_group: str


def settings_from_env() -> Settings:
    return Settings(
        base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
        model=os.getenv("VIRALAPI_MODEL", "gpt-4.1-mini"),
        timeout=float(os.getenv("VIRALAPI_TIMEOUT_SECONDS", "12")),
        max_attempts=int(os.getenv("VIRALAPI_MAX_ATTEMPTS", "2")),
        cost_group=os.getenv("VIRALAPI_COST_GROUP", "official_transfer"),
    )


def complete(prompt: str, tenant_id: str, dry_run: bool = False) -> str:
    request_id = str(uuid.uuid4())
    settings = settings_from_env()
    if dry_run:
        print(json.dumps({
            "request_id": request_id,
            "tenant_id": tenant_id,
            "route": {
                "base_url": settings.base_url,
                "model": settings.model,
                "timeout_seconds": settings.timeout,
                "max_attempts": settings.max_attempts,
                "cost_group": settings.cost_group,
            },
        }))
        return "dry-run"

    client = OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=settings.base_url,
        timeout=settings.timeout,
        max_retries=0,
    )
    for attempt in range(1, settings.max_attempts + 1):
        started = time.monotonic()
        try:
            response = client.chat.completions.create(
                model=settings.model,
                messages=[{"role": "user", "content": prompt}],
                extra_headers={"X-Request-ID": request_id, "X-Tenant-ID": tenant_id},
            )
            LOG.info(
                "sdk_success request_id=%s tenant_id=%s model=%s cost_group=%s attempt=%s latency_ms=%s",
                request_id, tenant_id, settings.model, settings.cost_group, attempt,
                round((time.monotonic() - started) * 1000),
            )
            return response.choices[0].message.content or ""
        except Exception as exc:  # SDK exception classes vary by version.
            status = getattr(exc, "status_code", None)
            LOG.warning(
                "sdk_error request_id=%s tenant_id=%s model=%s attempt=%s status=%s error=%s",
                request_id, tenant_id, settings.model, attempt, status, type(exc).__name__,
            )
            if status not in RETRYABLE or attempt == settings.max_attempts:
                raise RuntimeError(f"request failed request_id={request_id}") from exc
            time.sleep(0.5 * (2 ** (attempt - 1)))
    raise AssertionError("unreachable")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", default="startup-basic")
    parser.add_argument("--prompt", default="Summarize this support ticket.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(complete(args.prompt, args.tenant_id, args.dry_run))
