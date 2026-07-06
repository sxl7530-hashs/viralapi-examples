#!/usr/bin/env node
// OpenAI-compatible chat completions example for ViralAPI.
//
// Environment variables:
//   VIRALAPI_API_KEY   Your ViralAPI API key
//   VIRALAPI_BASE_URL  Your OpenAI-compatible base URL, e.g. https://your-endpoint/v1
//   VIRALAPI_MODEL     Optional model name configured for your account

const apiKey = process.env.VIRALAPI_API_KEY;
const baseUrl = process.env.VIRALAPI_BASE_URL;
const model = process.env.VIRALAPI_MODEL || "gpt-4o-mini";

if (!apiKey || !baseUrl) {
  console.error("Please set VIRALAPI_API_KEY and VIRALAPI_BASE_URL first.");
  process.exit(1);
}

const response = await fetch(`${baseUrl.replace(/\/$/, "")}/chat/completions`, {
  method: "POST",
  headers: {
    Authorization: `Bearer ${apiKey}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    model,
    messages: [
      { role: "system", content: "You are a concise API integration assistant." },
      {
        role: "user",
        content:
          "Explain how a small team can switch models without rewriting app code.",
      },
    ],
    temperature: 0.3,
  }),
});

if (!response.ok) {
  console.error(`HTTP ${response.status}`);
  console.error(await response.text());
  process.exit(1);
}

console.log(JSON.stringify(await response.json(), null, 2));
