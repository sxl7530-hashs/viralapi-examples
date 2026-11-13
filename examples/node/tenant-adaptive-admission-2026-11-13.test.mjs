import test from "node:test";
import assert from "node:assert/strict";
import { parseRetryAfterMs, TenantAdmission } from "./tenant-adaptive-admission-2026-11-13.mjs";

test("parses Retry-After delta seconds and HTTP dates", () => {
  assert.equal(parseRetryAfterMs("2.5"), 2500);
  assert.equal(parseRetryAfterMs("Fri, 13 Nov 2026 10:00:03 GMT", Date.parse("2026-11-13T10:00:00Z")), 3000);
});

test("invalid and elapsed Retry-After values are handled", () => {
  const nowMs = Date.parse("2026-11-13T10:00:00Z");
  assert.equal(parseRetryAfterMs("not-a-delay", nowMs), null);
  assert.equal(parseRetryAfterMs("1e309", nowMs), null);
  assert.equal(parseRetryAfterMs("Wed, 21 Oct 2015 07:28:00 GMT", nowMs), 0);
});

test("admission and throttle feedback are isolated by tenant", () => {
  const admission = new TenantAdmission({ initialLimit: 4, minLimit: 1, maxLimit: 8 });
  assert.equal(admission.acquire("a"), true);
  admission.recordThrottle("a", 500);
  assert.equal(admission.limit("a"), 2);
  assert.equal(admission.cooldownMs("a"), 500);
  assert.equal(admission.limit("b"), 4);
});

test("success increases capacity gradually and release is bounded", () => {
  const admission = new TenantAdmission({ initialLimit: 1, minLimit: 1, maxLimit: 2 });
  admission.recordSuccess("a");
  admission.recordSuccess("a");
  assert.equal(admission.limit("a"), 2);
  admission.release("unknown");
  assert.equal(admission.inFlight("unknown"), 0);
});