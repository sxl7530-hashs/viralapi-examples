import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.VIRALAPI_API_KEY,
  baseURL: process.env.VIRALAPI_BASE_URL || "https://viralapi.ai/v1",
  timeout: 8000,
  maxRetries: 0,
});

const ROUTES = {
  support: { model: "claude-sonnet", costGroup: "stable_official", timeout: 8000 },
  batch_draft: { model: "gemini-2.5-flash", costGroup: "welfare", timeout: 20000 },
};

export async function complete(feature, messages) {
  const route = ROUTES[feature] || ROUTES.support;
  const requestId = crypto.randomUUID();
  const started = Date.now();
  try {
    const response = await client.chat.completions.create(
      { model: route.model, messages, temperature: 0.2 },
      { timeout: route.timeout, headers: { "X-Request-ID": requestId, "X-Feature": feature } },
    );
    console.info(JSON.stringify({ requestId, feature, model: route.model,
      costGroup: route.costGroup, latencyMs: Date.now() - started, degraded: false }));
    return response.choices[0]?.message?.content || "";
  } catch (error) {
    console.warn(JSON.stringify({ requestId, feature, model: route.model,
      status: error.status, errorType: error.constructor.name }));
    throw error;
  }
}
