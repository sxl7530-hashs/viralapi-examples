#!/usr/bin/env python3
"""Validate ViralAPI launch configuration and optionally run a low-risk smoke test."""
import os
import sys
from urllib.parse import urlparse


def main() -> int:
    api_key = os.getenv("VIRALAPI_API_KEY", "")
    base_url = os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1")
    if not api_key:
        print("FAIL missing VIRALAPI_API_KEY")
        return 2
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        print("FAIL VIRALAPI_BASE_URL must be an https URL")
        return 2
    print({"check": "config", "host": parsed.netloc, "key_present": True})
    if os.getenv("VIRALAPI_PROBE_LIVE") != "1":
        print("PASS config-only; set VIRALAPI_PROBE_LIVE=1 for a live smoke test")
        return 0
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=8.0, max_retries=0)
        response = client.chat.completions.create(
            model=os.getenv("VIRALAPI_PROBE_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": "Reply with exactly: launch-probe-ok"}],
            max_tokens=16,
        )
        text = response.choices[0].message.content or ""
        print({"check": "live", "response_prefix": text[:32]})
        return 0
    except Exception as exc:
        print({"check": "live", "error_type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
