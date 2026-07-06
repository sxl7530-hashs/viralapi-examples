#!/usr/bin/env bash
set -euo pipefail

: "${VIRALAPI_API_KEY:?Please set VIRALAPI_API_KEY}"
: "${VIRALAPI_BASE_URL:?Please set VIRALAPI_BASE_URL, for example https://your-endpoint/v1}"

MODEL="${VIRALAPI_MODEL:-gpt-4o-mini}"

curl "${VIRALAPI_BASE_URL%/}/chat/completions" \
  -H "Authorization: Bearer ${VIRALAPI_API_KEY}" \
  -H "Content-Type: application/json" \
  -d @- <<JSON
{
  "model": "${MODEL}",
  "messages": [
    {
      "role": "system",
      "content": "You are a concise API integration assistant."
    },
    {
      "role": "user",
      "content": "Explain one benefit of using an OpenAI-compatible API gateway for Claude, GPT, and Gemini."
    }
  ],
  "temperature": 0.3
}
JSON
