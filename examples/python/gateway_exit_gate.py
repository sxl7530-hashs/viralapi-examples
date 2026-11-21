#!/usr/bin/env python3
"""Evaluate an LLM gateway pilot against explicit exit and compatibility gates."""
from dataclasses import dataclass, asdict
import json

REQUIRED_CAPABILITIES = {
    "chat", "streaming", "tool_calls", "json_mode", "usage", "error_mapping"
}


@dataclass(frozen=True)
class Pilot:
    required_capabilities: set[str]
    verified_capabilities: set[str]
    p95_latency_ms: int
    p95_limit_ms: int
    error_rate: float
    error_rate_limit: float
    gateway_monthly_cost: float
    direct_monthly_cost: float
    monthly_engineering_hours_saved: float
    engineer_hourly_cost: float
    monthly_migration_reserve: float
    has_exportable_logs: bool
    has_direct_fallback: bool


def evaluate(pilot: Pilot) -> dict:
    missing = sorted(pilot.required_capabilities - pilot.verified_capabilities)
    monthly_net_value = (
        pilot.direct_monthly_cost - pilot.gateway_monthly_cost
        + pilot.monthly_engineering_hours_saved * pilot.engineer_hourly_cost
        - pilot.monthly_migration_reserve
    )
    gates = {
        "capabilities": not missing,
        "latency": pilot.p95_latency_ms <= pilot.p95_limit_ms,
        "errors": pilot.error_rate <= pilot.error_rate_limit,
        "logs_exportable": pilot.has_exportable_logs,
        "direct_fallback": pilot.has_direct_fallback,
        "positive_monthly_value": monthly_net_value > 0,
    }
    return {
        "gates": gates,
        "missing_capabilities": missing,
        "monthly_net_value": round(monthly_net_value, 2),
        "decision": "continue_pilot" if all(gates.values()) else "hold_or_exit",
    }


if __name__ == "__main__":
    sample = Pilot(
        required_capabilities=REQUIRED_CAPABILITIES,
        verified_capabilities=REQUIRED_CAPABILITIES,
        p95_latency_ms=850,
        p95_limit_ms=1200,
        error_rate=0.008,
        error_rate_limit=0.01,
        gateway_monthly_cost=1400,
        direct_monthly_cost=1750,
        monthly_engineering_hours_saved=3,
        engineer_hourly_cost=80,
        monthly_migration_reserve=250,
        has_exportable_logs=True,
        has_direct_fallback=True,
    )
    print(json.dumps(evaluate(sample), indent=2))
