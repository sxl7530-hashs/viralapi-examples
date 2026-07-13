"""Small OpenAI-compatible client with bounded retry and model fallback."""
from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import Sequence

from openai import OpenAI

logger = logging.getLogger("viralapi.llm")


def complete(messages: Sequence[dict[str, str]], request_id: str) -> str:
    client = OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
        timeout=float(os.getenv("VIRALAPI_TIMEOUT_SECONDS", "20")),
        max_retries=0,
    )
    models = [
        os.getenv("PRIMARY_MODEL", "claude-sonnet-4"),
        os.getenv("FALLBACK_MODEL", "gpt-4o-mini"),
    ]
    last_error: Exception | None = None
    for model in models:
        for attempt in range(3):
            started = time.monotonic()
            try:
                result = client.chat.completions.create(
                    model=model,
                    messages=list(messages),
                    temperature=0.2,
                    extra_headers={"X-Request-ID": request_id},
                )
                logger.info("llm_success request_id=%s model=%s attempt=%d latency_ms=%d",
                            request_id, model, attempt + 1,
                            round((time.monotonic() - started) * 1000))
                return result.choices[0].message.content or ""
            except Exception as exc:  # SDK exceptions differ between versions.
                last_error = exc
                logger.warning("llm_error request_id=%s model=%s attempt=%d error=%s",
                               request_id, model, attempt + 1, type(exc).__name__)
                if attempt < 2:
                    time.sleep(min(8.0, 0.5 * (2**attempt)) + random.random() * 0.2)
    raise RuntimeError(f"all configured models failed: {request_id}") from last_error


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(complete([{"role": "user", "content": "Summarize this incident."}], "demo-001"))
