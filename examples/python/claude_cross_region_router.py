"""Scenario-based OpenAI-compatible router for Claude/GPT/Gemini traffic."""
from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import Sequence

from openai import OpenAI

logger = logging.getLogger("viralapi.router")

SCENARIO_MODELS: dict[str, list[str]] = {
    "support": ["claude-sonnet-4", "gpt-4o-mini"],
    "content_batch": ["claude-sonnet-4", "gemini-2.5-flash"],
    "analytics": ["claude-sonnet-4", "gpt-4.1-mini"],
    "internal_tool": ["claude-sonnet-4", "gpt-4o-mini"],
}


def build_client() -> OpenAI:
    return OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
        timeout=float(os.getenv("VIRALAPI_TIMEOUT_SECONDS", "20")),
        max_retries=0,
    )


def chat_with_routing(
    messages: Sequence[dict[str, str]],
    scenario: str,
    request_id: str,
) -> str:
    client = build_client()
    models = SCENARIO_MODELS.get(scenario, ["claude-sonnet-4", "gpt-4o-mini"])
    last_error: Exception | None = None

    for model in models:
        for attempt in range(3):
            started = time.monotonic()
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=list(messages),
                    temperature=0.2,
                    extra_headers={"X-Request-ID": request_id},
                )
                latency_ms = round((time.monotonic() - started) * 1000)
                logger.info(
                    "llm_success request_id=%s scenario=%s model=%s attempt=%d latency_ms=%d",
                    request_id,
                    scenario,
                    model,
                    attempt + 1,
                    latency_ms,
                )
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                latency_ms = round((time.monotonic() - started) * 1000)
                logger.warning(
                    "llm_error request_id=%s scenario=%s model=%s attempt=%d latency_ms=%d error=%s",
                    request_id,
                    scenario,
                    model,
                    attempt + 1,
                    latency_ms,
                    type(exc).__name__,
                )
                if attempt < 2:
                    backoff = min(8.0, 0.5 * (2**attempt)) + random.random() * 0.2
                    time.sleep(backoff)

    raise RuntimeError(
        f"all configured models failed for scenario={scenario} request_id={request_id}"
    ) from last_error


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(
        chat_with_routing(
            [{"role": "user", "content": "Summarize this support incident."}],
            scenario="support",
            request_id="demo-claude-router-001",
        )
    )
