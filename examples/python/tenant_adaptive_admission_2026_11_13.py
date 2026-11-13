"""Tenant-isolated adaptive admission and Retry-After parsing example."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import isfinite
from threading import Lock
from typing import Dict, Optional


def parse_retry_after_ms(value: Optional[str], now: Optional[datetime] = None) -> Optional[int]:
    if value is None:
        return None
    try:
        seconds = float(value)
        if not isfinite(seconds) or seconds < 0:
            return None
        return int(seconds * 1000)
    except (ValueError, OverflowError):
        pass

    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return max(0, int((retry_at - current).total_seconds() * 1000))
    except (TypeError, ValueError, OverflowError):
        return None


class TenantAdmission:
    """A process-local concurrency controller; use a shared store across workers."""

    def __init__(self, initial_limit=4, min_limit=1, max_limit=32):
        if min_limit < 1 or not min_limit <= initial_limit <= max_limit:
            raise ValueError("require 1 <= min_limit <= initial_limit <= max_limit")
        self._initial = initial_limit
        self._minimum = min_limit
        self._maximum = max_limit
        self._state: Dict[str, Dict[str, int]] = {}
        self._lock = Lock()

    def _tenant(self, tenant_id):
        if not tenant_id:
            raise ValueError("tenant_id is required")
        return self._state.setdefault(
            tenant_id, {"limit": self._initial, "in_flight": 0,
                        "successes": 0, "cooldown_ms": 0}
        )

    def acquire(self, tenant_id):
        with self._lock:
            state = self._tenant(tenant_id)
            if state["in_flight"] >= state["limit"]:
                return False
            state["in_flight"] += 1
            return True

    def release(self, tenant_id):
        with self._lock:
            state = self._tenant(tenant_id)
            state["in_flight"] = max(0, state["in_flight"] - 1)

    def record_success(self, tenant_id):
        with self._lock:
            state = self._tenant(tenant_id)
            state["successes"] += 1
            if state["successes"] >= state["limit"]:
                state["limit"] = min(self._maximum, state["limit"] + 1)
                state["successes"] = 0
            state["cooldown_ms"] = 0

    def record_throttle(self, tenant_id, retry_after_ms=None):
        with self._lock:
            state = self._tenant(tenant_id)
            state["limit"] = max(self._minimum, state["limit"] // 2)
            state["successes"] = 0
            state["cooldown_ms"] = max(0, retry_after_ms or 0)

    def limit(self, tenant_id):
        with self._lock:
            return self._tenant(tenant_id)["limit"]

    def in_flight(self, tenant_id):
        with self._lock:
            return self._tenant(tenant_id)["in_flight"]

    def cooldown_ms(self, tenant_id):
        with self._lock:
            return self._tenant(tenant_id)["cooldown_ms"]