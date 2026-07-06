#!/usr/bin/env python3
"""OpenAI-compatible chat completions example for ViralAPI.

Environment variables:
  VIRALAPI_API_KEY   Your ViralAPI API key
  VIRALAPI_BASE_URL  Your OpenAI-compatible base URL, e.g. https://your-endpoint/v1
  VIRALAPI_MODEL     Optional model name configured for your account
"""

import json
import os
import sys
import urllib.request

api_key = os.environ.get("VIRALAPI_API_KEY")
base_url = os.environ.get("VIRALAPI_BASE_URL")
model = os.environ.get("VIRALAPI_MODEL", "gpt-4o-mini")

if not api_key or not base_url:
    sys.exit("Please set VIRALAPI_API_KEY and VIRALAPI_BASE_URL first.")

url = base_url.rstrip("/") + "/chat/completions"
payload = {
    "model": model,
    "messages": [
        {"role": "system", "content": "You are a concise API integration assistant."},
        {
            "role": "user",
            "content": "Give a short checklist for testing an OpenAI-compatible API gateway.",
        },
    ],
    "temperature": 0.3,
}

request = urllib.request.Request(
    url,
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    },
    method="POST",
)

with urllib.request.urlopen(request, timeout=60) as response:
    body = response.read().decode("utf-8")
    print(json.dumps(json.loads(body), ensure_ascii=False, indent=2))
