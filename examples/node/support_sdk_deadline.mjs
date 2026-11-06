// Node.js 20+, npm install openai. Server-side only.
import OpenAI from 'openai';
import { pathToFileURL } from 'node:url';

// Bounded admission: reject excess work instead of growing an unbounded queue.
export function createDraftService(client, model, { concurrency = 8, deadlineMs = 8000 } = {}) {
  let active = 0;
  return async function draft(messages) {
    if (active >= concurrency) throw new Error('busy');
    active++;
    const started = performance.now();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), deadlineMs);
    let status = 'error';
    try {
      const result = await client.chat.completions.create(
        { model, messages, max_tokens: 256 }, { signal: controller.signal });
      const choice = result.choices?.[0];
      if (choice?.finish_reason !== 'stop' || !choice.message?.content?.trim()) {
        throw new Error('invalid_output');
      }
      status = 'ok';
      return choice.message.content;
    } finally {
      clearTimeout(timer);
      active--;
      console.log(JSON.stringify({ scenario: 'support_draft', model, status,
        elapsed_ms: Math.round(performance.now() - started), attempt: 1 }));
    }
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  for (const name of ['VIRALAPI_API_KEY', 'VIRALAPI_BASE_URL', 'VIRALAPI_MODEL']) {
    if (!process.env[name]) throw new Error(`Missing environment variable: ${name}`);
  }
  const client = new OpenAI({ apiKey: process.env.VIRALAPI_API_KEY,
    baseURL: process.env.VIRALAPI_BASE_URL, maxRetries: 0, timeout: 5000 });
  const draft = createDraftService(client, process.env.VIRALAPI_MODEL);
  try {
    await draft([{ role: 'user', content: 'Draft a short support acknowledgement.' }]);
  } catch {
    console.error('Draft failed; inspect sanitized status and transfer to human support.');
    process.exitCode = 1;
  }
}
// Abort does not prove upstream execution or billing stopped. No automatic replay.
// Concurrency limits do not enforce RPM/TPM or shared tenant budgets.
