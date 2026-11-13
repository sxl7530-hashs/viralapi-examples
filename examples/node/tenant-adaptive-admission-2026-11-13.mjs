export function parseRetryAfterMs(value, nowMs = Date.now()) {
  if (typeof value !== "string" || !value.trim()) return null;
  const trimmed = value.trim();
  if (/^\d+(?:\.\d+)?$/.test(trimmed)) {
    const delay = Number(trimmed) * 1000;
    return Number.isFinite(delay) ? Math.ceil(delay) : null;
  }
  const date = Date.parse(trimmed);
  return Number.isNaN(date) ? null : Math.max(0, date - nowMs);
}

export class TenantAdmission {
  constructor({ initialLimit = 4, minLimit = 1, maxLimit = 32 } = {}) {
    if (![initialLimit, minLimit, maxLimit].every(Number.isInteger) || minLimit < 1 || initialLimit < minLimit || maxLimit < initialLimit) {
      throw new RangeError("limits must be integers with 1 <= minLimit <= initialLimit <= maxLimit");
    }
    this.defaults = { initialLimit, minLimit, maxLimit };
    this.tenants = new Map();
  }

  state(tenant) {
    if (tenant === undefined || tenant === null || tenant === "") throw new TypeError("tenant is required");
    if (!this.tenants.has(tenant)) {
      this.tenants.set(tenant, { limit: this.defaults.initialLimit, inFlight: 0, successes: 0, cooldownMs: 0 });
    }
    return this.tenants.get(tenant);
  }

  acquire(tenant) {
    const state = this.state(tenant);
    if (state.inFlight >= state.limit) return false;
    state.inFlight += 1;
    return true;
  }

  release(tenant) {
    const state = this.state(tenant);
    state.inFlight = Math.max(0, state.inFlight - 1);
  }

  recordSuccess(tenant) {
    const state = this.state(tenant);
    state.successes += 1;
    state.cooldownMs = 0;
    if (state.successes >= state.limit && state.limit < this.defaults.maxLimit) {
      state.limit += 1;
      state.successes = 0;
    }
  }

  recordThrottle(tenant, retryAfterMs = null) {
    const state = this.state(tenant);
    state.limit = Math.max(this.defaults.minLimit, Math.floor(state.limit / 2));
    state.successes = 0;
    state.cooldownMs = Number.isFinite(retryAfterMs) ? Math.max(0, retryAfterMs) : 0;
  }

  limit(tenant) { return this.state(tenant).limit; }
  inFlight(tenant) { return this.state(tenant).inFlight; }
  cooldownMs(tenant) { return this.state(tenant).cooldownMs; }
}