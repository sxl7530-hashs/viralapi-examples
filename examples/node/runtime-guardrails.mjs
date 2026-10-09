#!/usr/bin/env node

import crypto from "node:crypto";

const RETRYABLE_STATUS = new Set([408, 425, 429, 500, 502, 503, 504]);

function parseRetryAfter(value, now = Date.now()) {
  if (!value) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000);
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? null : Math.max(0, timestamp - now);
}

function logEvent(fields) {
  const forbidden = new Set(["api_key", "authorization", "prompt", "response_body"]);
  const safe = Object.fromEntries(
    Object.entries(fields).filter(([key]) => !forbidden.has(key.toLowerCase())),
  );
  console.log(JSON.stringify(safe));
}

class TokenBucket {
  constructor({ ratePerSecond, burst }) {
    this.ratePerSecond = ratePerSecond;
    this.capacity = burst;
    this.tokens = burst;
    this.updatedAt = performance.now();
  }

  allow() {
    const now = performance.now();
    this.tokens = Math.min(
      this.capacity,
      this.tokens + ((now - this.updatedAt) / 1000) * this.ratePerSecond,
    );
    this.updatedAt = now;
    if (this.tokens < 1) return false;
    this.tokens -= 1;
    return true;
  }
}

class TransportError extends Error {
  constructor(status, retryAfter = null) {
    super(`simulated HTTP ${status}`);
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

function fakeTransport(sequence) {
  let index = 0;
  return async ({ idempotencyKey, signal }) => {
    void idempotencyKey;
    void signal;
    const status = sequence[index++] ?? 200;
    if (status !== 200) throw new TransportError(status, "0");
    return { choices: [{ message: { content: "simulated model result" } }] };
  };
}

async function complete({ tenantId, scenario, idempotencyKey, totalDeadlineMs, maxAttempts, limiter, transport }) {
  if (!limiter.allow()) throw new Error("tenant_rate_limited_before_provider_call");
  const requestId = crypto.randomUUID();
  const deadline = performance.now() + totalDeadlineMs;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const remainingMs = deadline - performance.now();
    if (remainingMs <= 250) throw new Error(`deadline_exhausted request_id=${requestId}`);
    const started = performance.now();
    try {
      const response = await transport({
        idempotencyKey,
        signal: AbortSignal.timeout(Math.floor(Math.min(5000, remainingMs - 250))),
      });
      if (performance.now() >= deadline) {
        throw new Error(`deadline_exhausted_after_provider_return request_id=${requestId}`);
      }
      logEvent({
        event: "llm_call_ok",
        request_id: requestId,
        tenant_id: tenantId,
        scenario,
        attempt,
        latency_ms: performance.now() - started,
        deadline_remaining_ms: deadline - performance.now(),
      });
      return response.choices[0].message.content;
    } catch (error) {
      const statusCode = error.status ?? 0;
      const retryable = statusCode === 0 || RETRYABLE_STATUS.has(statusCode);
      logEvent({
        event: "llm_call_error",
        request_id: requestId,
        tenant_id: tenantId,
        scenario,
        attempt,
        status_code: statusCode,
        retryable,
      });
      if (!retryable || attempt === maxAttempts) throw error;
      const serverDelay = parseRetryAfter(error.retryAfter);
      const delayMs = serverDelay > 0 ? serverDelay : 200 + Math.random() * Math.min(1600, 400 * 2 ** attempt);
      if (performance.now() + delayMs + 250 >= deadline) {
        throw new Error(`retry_would_exceed_deadline request_id=${requestId}`);
      }
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }
  throw new Error("unreachable");
}

async function main() {
  const result = await complete({
    tenantId: "tenant-demo",
    scenario: "ai_support",
    idempotencyKey: "tenant-demo:ticket-42:v1",
    totalDeadlineMs: 3000,
    maxAttempts: 2,
    limiter: new TokenBucket({ ratePerSecond: 2, burst: 4 }),
    transport: fakeTransport(process.argv.includes("--simulate-429") ? [429, 200] : [200]),
  });
  console.log(result);
}

async function sleep(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function selfTest() {
  if (parseRetryAfter("NaN") !== null || parseRetryAfter("Infinity") !== null) {
    throw new Error("non-finite Retry-After must be ignored");
  }
  const recovered = await complete({
    tenantId: "test", scenario: "test", idempotencyKey: "stable-key",
    totalDeadlineMs: 3000, maxAttempts: 2,
    limiter: new TokenBucket({ ratePerSecond: 10, burst: 2 }),
    transport: fakeTransport([429, 200]),
  });
  if (recovered !== "simulated model result") throw new Error("429 did not recover");

  let fatalCalls = 0;
  try {
    await complete({
      tenantId: "test", scenario: "test", idempotencyKey: "stable-key",
      totalDeadlineMs: 3000, maxAttempts: 3,
      limiter: new TokenBucket({ ratePerSecond: 10, burst: 2 }),
      transport: async () => { fatalCalls += 1; throw new TransportError(401); },
    });
  } catch (error) {
    if (error.status !== 401) throw error;
  }
  if (fatalCalls !== 1) throw new Error("401 must not retry");

  let providerCalls = 0;
  try {
    await complete({
      tenantId: "test", scenario: "test", idempotencyKey: "stable-key",
      totalDeadlineMs: 3000, maxAttempts: 2,
      limiter: new TokenBucket({ ratePerSecond: 0, burst: 0 }),
      transport: async () => { providerCalls += 1; return { choices: [{ message: { content: "bad" } }] }; },
    });
  } catch (error) {
    if (error.message !== "tenant_rate_limited_before_provider_call") throw error;
  }
  if (providerCalls !== 0) throw new Error("limiter called provider");

  try {
    await complete({
      tenantId: "test", scenario: "test", idempotencyKey: "stable-key",
      totalDeadlineMs: 1000, maxAttempts: 2,
      limiter: new TokenBucket({ ratePerSecond: 10, burst: 2 }),
      transport: async () => { throw new TransportError(429, "999999"); },
    });
  } catch (error) {
    if (!error.message.startsWith("retry_would_exceed_deadline")) throw error;
  }
  console.log("self-test: ok");
}

if (process.argv.includes("--self-test")) {
  await selfTest();
} else {
  await main();
}