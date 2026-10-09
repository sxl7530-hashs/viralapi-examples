#!/usr/bin/env python3
"""Production guardrails around an OpenAI-compatible SDK call.

The default demo uses a deterministic fake transport and needs no API key.
Set VIRALAPI_LIVE=1 and install openai to exercise the live transport.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import threading
import time
import uuid
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Callable


RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
SECRET_FIELDS = {"authorization", "prompt", "response_body"}


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model: str
    total_deadline_ms: int
    per_try_timeout_ms: int
    max_attempts: int

    @classmethod
    def from_env(cls, *, live: bool) -> "Settings":
        api_key = os.getenv("VIRALAPI_API_KEY", "")
        if live and not api_key:
            raise ValueError("VIRALAPI_API_KEY is required when VIRALAPI_LIVE=1")
        settings = cls(
            api_key=api_key,
            base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
            model=os.getenv("VIRALAPI_MODEL", "gpt-4o-mini"),
            total_deadline_ms=int(os.getenv("VIRALAPI_DEADLINE_MS", "12000")),
            per_try_timeout_ms=int(os.getenv("VIRALAPI_PER_TRY_TIMEOUT_MS", "5000")),
            max_attempts=int(os.getenv("VIRALAPI_MAX_ATTEMPTS", "2")),
        )
        if not settings.base_url.startswith("https://"):
            raise ValueError("VIRALAPI_BASE_URL must use https")
        if not settings.model.strip():
            raise ValueError("VIRALAPI_MODEL must be non-empty")
        if settings.total_deadline_ms <= 0 or settings.per_try_timeout_ms <= 0:
            raise ValueError("timeouts must be positive")
        if settings.max_attempts not in {1, 2, 3}:
            raise ValueError("VIRALAPI_MAX_ATTEMPTS must be between 1 and 3")
        if settings.per_try_timeout_ms >= settings.total_deadline_ms:
            raise ValueError("per-try timeout must be smaller than total deadline")
        return settings


class TokenBucket:
    """Single-process demo; use Redis or gateway limits in multi-instance apps."""

    def __init__(self, *, rate_per_second: float, burst: int) -> None:
        self.rate = rate_per_second
        self.capacity = float(burst)
        self.tokens = float(burst)
        self.updated_at = time.monotonic()
        self.lock = threading.Lock()

    def allow(self) -> bool:
        with self.lock:
            now = time.monotonic()
            self.tokens = min(
                self.capacity,
                self.tokens + (now - self.updated_at) * self.rate,
            )
            self.updated_at = now
            if self.tokens < 1:
                return False
            self.tokens -= 1
            return True


class TransportError(Exception):
    def __init__(self, status: int, message: str, retry_after: str | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def retry_after_seconds(value: str | None, *, now: float | None = None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        try:
            target = parsedate_to_datetime(value).timestamp()
            delay = target - (now if now is not None else time.time())
            return max(0.0, delay) if math.isfinite(delay) else None
        except (TypeError, ValueError, OverflowError):
            return None


def log_event(**fields: Any) -> None:
    clean = {key: value for key, value in fields.items() if key.lower() not in SECRET_FIELDS}
    print(json.dumps(clean, ensure_ascii=True, sort_keys=True))


def call_with_guardrails(
    *,
    settings: Settings,
    tenant_id: str,
    scenario: str,
    idempotency_key: str,
    limiter: TokenBucket,
    transport: Callable[[int, str], str],
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    if not limiter.allow():
        raise RuntimeError("tenant_rate_limited_before_provider_call")

    request_id = str(uuid.uuid4())
    deadline = time.monotonic() + settings.total_deadline_ms / 1000

    for attempt in range(1, settings.max_attempts + 1):
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 250:
            raise TimeoutError(f"deadline_exhausted request_id={request_id}")
        timeout_ms = min(settings.per_try_timeout_ms, remaining_ms - 250)
        started = time.monotonic()
        try:
            result = transport(timeout_ms, idempotency_key)
            if time.monotonic() >= deadline:
                raise TimeoutError(f"deadline_exhausted_after_provider_return request_id={request_id}")
            log_event(
                event="llm_call_ok",
                request_id=request_id,
                tenant_id=tenant_id,
                scenario=scenario,
                model=settings.model,
                attempt=attempt,
                latency_ms=round((time.monotonic() - started) * 1000),
                deadline_remaining_ms=int((deadline - time.monotonic()) * 1000),
                idempotency_key_hash=uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key).hex[:12],
            )
            return result
        except TransportError as exc:
            retryable = exc.status == 0 or exc.status in RETRYABLE_STATUS
            log_event(
                event="llm_call_error",
                request_id=request_id,
                tenant_id=tenant_id,
                scenario=scenario,
                model=settings.model,
                attempt=attempt,
                status_code=exc.status,
                retryable=retryable,
                error_type=type(exc).__name__,
            )
            if not retryable or attempt == settings.max_attempts:
                raise

            server_delay = retry_after_seconds(exc.retry_after)
            jitter_delay = random.uniform(0.2, min(2.0, 0.4 * (2**attempt)))
            delay = server_delay if server_delay is not None and server_delay > 0 else jitter_delay
            if time.monotonic() + delay + 0.25 >= deadline:
                raise TimeoutError(f"retry_would_exceed_deadline request_id={request_id}") from exc
            sleep(delay)

    raise AssertionError("attempt loop ended unexpectedly")


def fake_transport_factory(sequence: list[int]) -> Callable[[int, str], str]:
    calls = iter(sequence)

    def transport(timeout_ms: int, idempotency_key: str) -> str:
        del timeout_ms, idempotency_key
        status = next(calls, 200)
        if status != 200:
            raise TransportError(status, f"simulated HTTP {status}", "0")
        return "simulated model result"

    return transport


def live_transport(settings: Settings, messages: list[dict[str, str]]) -> Callable[[int, str], str]:
    from openai import OpenAI

    client = OpenAI(api_key=settings.api_key, base_url=settings.base_url, max_retries=0)

    def transport(timeout_ms: int, idempotency_key: str) -> str:
        try:
            response = client.chat.completions.create(
                model=settings.model,
                messages=messages,
                timeout=timeout_ms / 1000,
                extra_headers={"Idempotency-Key": idempotency_key},
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            status = int(getattr(exc, "status_code", 0) or 0)
            headers = getattr(getattr(exc, "response", None), "headers", {})
            raise TransportError(status, type(exc).__name__, headers.get("retry-after")) from exc

    return transport


def self_test() -> None:
    assert retry_after_seconds("2") == 2
    assert retry_after_seconds("bad") is None
    assert retry_after_seconds("nan") is None
    assert retry_after_seconds("inf") is None
    settings = Settings("", "https://viralapi.ai/v1", "demo", 3000, 1000, 2)
    result = call_with_guardrails(
        settings=settings,
        tenant_id="tenant-demo",
        scenario="batch_content",
        idempotency_key="tenant-demo:job-42",
        limiter=TokenBucket(rate_per_second=10, burst=2),
        transport=fake_transport_factory([429, 200]),
        sleep=lambda _: None,
    )
    assert result == "simulated model result"

    calls = 0
    def fatal401(timeout_ms: int, idempotency_key: str) -> str:
        nonlocal calls
        calls += 1
        raise TransportError(401, "unauthorized")
    try:
        call_with_guardrails(settings=settings, tenant_id="tenant-demo", scenario="test",
                             idempotency_key="stable-key", limiter=TokenBucket(rate_per_second=10, burst=2),
                             transport=fatal401, sleep=lambda _: None)
    except TransportError as exc:
        assert exc.status == 401
    else:
        raise AssertionError("401 must not retry")
    assert calls == 1

    provider_calls = 0
    def should_not_call(timeout_ms: int, idempotency_key: str) -> str:
        nonlocal provider_calls
        provider_calls += 1
        return "unexpected"
    try:
        call_with_guardrails(settings=settings, tenant_id="tenant-demo", scenario="test",
                             idempotency_key="stable-key", limiter=TokenBucket(rate_per_second=0, burst=0),
                             transport=should_not_call, sleep=lambda _: None)
    except RuntimeError as exc:
        assert str(exc) == "tenant_rate_limited_before_provider_call"
    else:
        raise AssertionError("limiter must reject")
    assert provider_calls == 0

    try:
        call_with_guardrails(settings=Settings("", "https://viralapi.ai/v1", "demo", 1000, 900, 2),
                             tenant_id="tenant-demo", scenario="test", idempotency_key="stable-key",
                             limiter=TokenBucket(rate_per_second=10, burst=2),
                             transport=lambda timeout_ms, key: (_ for _ in ()).throw(
                                 TransportError(429, "busy", "999999")
                             ), sleep=lambda _: None)
    except TimeoutError:
        pass
    else:
        raise AssertionError("large Retry-After must exhaust deadline")

    first = uuid.uuid5(uuid.NAMESPACE_URL, "stable-key").hex
    assert first == uuid.uuid5(uuid.NAMESPACE_URL, "stable-key").hex
    print("self-test: ok")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--simulate", choices=["success", "429-then-success"], default="success")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return

    live = os.getenv("VIRALAPI_LIVE") == "1"
    settings = Settings.from_env(live=live)
    messages = [{"role": "user", "content": "Summarize the support ticket."}]
    transport = (
        live_transport(settings, messages)
        if live
        else fake_transport_factory([429, 200] if args.simulate == "429-then-success" else [200])
    )
    result = call_with_guardrails(
        settings=settings,
        tenant_id="tenant-demo",
        scenario="ai_support",
        idempotency_key="tenant-demo:ticket-42:v1",
        limiter=TokenBucket(rate_per_second=2, burst=4),
        transport=transport,
    )
    print(result)


if __name__ == "__main__":
    main()