import crypto from "node:crypto";
import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.VIRALAPI_API_KEY,
  baseURL: process.env.VIRALAPI_BASE_URL || "https://viralapi.ai/v1",
  timeout: Number(process.env.VIRALAPI_TIMEOUT_MS || 12000),
  maxRetries: 0,
});

const routes = {
  support: [
    { model: "claude-sonnet-4", group: "stable_official", timeoutMs: 10000 },
    { model: "gpt-4o-mini", group: "official_transfer", timeoutMs: 8000 },
  ],
  batch: [
    { model: "gemini-2.5-flash", group: "welfare", timeoutMs: 20000 },
    { model: "gpt-4o-mini", group: "official_transfer", timeoutMs: 12000 },
  ],
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const retryable = (status) => [408, 425, 429, 500, 502, 503, 504, "ETIMEDOUT"].includes(status);

export async function complete({ tenantId, scenario, messages }) {
  const requestId = crypto.randomUUID();
  const deadline = Date.now() + (scenario === "support" ? 25000 : 90000);

  for (const [fallbackIndex, route] of routes[scenario].entries()) {
    for (let attempt = 1; attempt <= 2 && Date.now() < deadline; attempt += 1) {
      try {
        const response = await client.chat.completions.create({
          model: route.model,
          messages,
          timeout: Math.min(route.timeoutMs, Math.max(deadline - Date.now(), 1000)),
        });
        console.log(JSON.stringify({ event: "llm_call_ok", request_id: requestId,
          tenant_id: tenantId, scenario, model: route.model, cost_group: route.group,
          fallback_index: fallbackIndex, attempt, degraded: fallbackIndex > 0 }));
        return response.choices[0].message.content;
      } catch (error) {
        const status = error.status || error.code;
        console.log(JSON.stringify({ event: "llm_call_error", request_id: requestId,
          tenant_id: tenantId, scenario, model: route.model, cost_group: route.group,
          fallback_index: fallbackIndex, attempt, status }));
        if (!retryable(status)) throw error;
        if (attempt < 2) await sleep(Math.min(1000 * 2 ** attempt, 4000));
      }
    }
  }
  throw new Error(`llm_deadline_exceeded request_id=${requestId}`);
}
