import crypto from "node:crypto";
import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.VIRALAPI_API_KEY,
  baseURL: process.env.VIRALAPI_BASE_URL || "https://viralapi.ai/v1",
  timeout: Number(process.env.VIRALAPI_TIMEOUT_MS || 20000),
});

const routes = {
  internal_tool: [
    { model: "gemini-2.5-flash", group: "welfare", timeoutMs: 25000 },
    { model: "gpt-4o-mini", group: "official_transfer", timeoutMs: 18000 },
  ],
  paid_saas_feature: [
    { model: "claude-sonnet-4", group: "stable_official", timeoutMs: 12000 },
    { model: "gpt-4o-mini", group: "official_transfer", timeoutMs: 10000 },
  ],
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function logEvent(fields) {
  console.log(JSON.stringify(fields));
}

export async function completeWithFallback({ tenantId, scenario, messages }) {
  const requestId = crypto.randomUUID();

  for (const [fallbackIndex, route] of routes[scenario].entries()) {
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      const started = Date.now();
      try {
        const response = await client.chat.completions.create({
          model: route.model,
          messages,
          timeout: route.timeoutMs,
        });
        logEvent({
          event: "llm_call_ok",
          request_id: requestId,
          tenant_id: tenantId,
          scenario,
          model: route.model,
          cost_group: route.group,
          fallback_index: fallbackIndex,
          attempt,
          latency_ms: Date.now() - started,
        });
        return response.choices[0].message.content;
      } catch (error) {
        const status = error.status || error.code;
        logEvent({
          event: "llm_call_error",
          request_id: requestId,
          tenant_id: tenantId,
          scenario,
          model: route.model,
          cost_group: route.group,
          fallback_index: fallbackIndex,
          attempt,
          status,
        });
        if (status === 429 || status === "ETIMEDOUT" || status >= 500) {
          await sleep(Math.min(1000 * 2 ** attempt, 6000));
          continue;
        }
        throw error;
      }
    }
  }

  throw new Error(`all LLM routes failed: request_id=${requestId}`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const content = await completeWithFallback({
    tenantId: "tenant_demo",
    scenario: "internal_tool",
    messages: [{ role: "user", content: "Draft a concise customer support macro." }],
  });
  console.log(content);
}
