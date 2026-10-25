#!/usr/bin/env python3
"""Estimate 90-day TCO for direct official APIs vs a compatible gateway.

No network access or third-party packages required.
"""
from dataclasses import dataclass, asdict
import json

@dataclass(frozen=True)
class Workload:
    monthly_requests: int
    engineer_hourly_cost: float
    incident_hourly_loss: float
    direct_build_hours: float
    gateway_build_hours: float
    direct_ops_hours_monthly: float
    gateway_ops_hours_monthly: float
    direct_incident_hours_monthly: float
    gateway_incident_hours_monthly: float
    direct_model_spend_monthly: float
    gateway_model_spend_monthly: float
    migration_probability: float
    direct_migration_hours: float
    gateway_migration_hours: float


def tco_90d(w: Workload) -> dict:
    def option(build, ops, incidents, spend, migration):
        engineering = (build + 3 * ops + w.migration_probability * migration) * w.engineer_hourly_cost
        outage = 3 * incidents * w.incident_hourly_loss
        model = 3 * spend
        return {"engineering": round(engineering, 2), "outage": round(outage, 2),
                "model": round(model, 2), "total": round(engineering + outage + model, 2)}
    direct = option(w.direct_build_hours, w.direct_ops_hours_monthly,
                    w.direct_incident_hours_monthly, w.direct_model_spend_monthly,
                    w.direct_migration_hours)
    gateway = option(w.gateway_build_hours, w.gateway_ops_hours_monthly,
                     w.gateway_incident_hours_monthly, w.gateway_model_spend_monthly,
                     w.gateway_migration_hours)
    winner = "gateway" if gateway["total"] < direct["total"] else "direct"
    return {"assumptions": asdict(w), "direct": direct, "gateway": gateway,
            "recommended": winner, "difference": round(abs(direct["total"]-gateway["total"]), 2)}

if __name__ == "__main__":
    sample = Workload(
        monthly_requests=300_000, engineer_hourly_cost=250, incident_hourly_loss=1200,
        direct_build_hours=80, gateway_build_hours=24,
        direct_ops_hours_monthly=28, gateway_ops_hours_monthly=10,
        direct_incident_hours_monthly=6, gateway_incident_hours_monthly=3,
        direct_model_spend_monthly=18000, gateway_model_spend_monthly=14500,
        migration_probability=0.35, direct_migration_hours=60, gateway_migration_hours=12)
    print(json.dumps(tco_90d(sample), ensure_ascii=False, indent=2))
