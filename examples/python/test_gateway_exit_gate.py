import unittest

from gateway_exit_gate import Pilot, REQUIRED_CAPABILITIES, evaluate


class ExitGateTests(unittest.TestCase):
    def pilot(self, **overrides):
        values = dict(
            required_capabilities=REQUIRED_CAPABILITIES,
            verified_capabilities=REQUIRED_CAPABILITIES,
            p95_latency_ms=800,
            p95_limit_ms=1200,
            error_rate=0.005,
            error_rate_limit=0.01,
            gateway_monthly_cost=1400,
            direct_monthly_cost=1750,
            monthly_engineering_hours_saved=3,
            engineer_hourly_cost=80,
            monthly_migration_reserve=250,
            has_exportable_logs=True,
            has_direct_fallback=True,
        )
        values.update(overrides)
        return Pilot(**values)

    def test_passes_when_all_gates_and_value_pass(self):
        result = evaluate(self.pilot())
        self.assertEqual(result["decision"], "continue_pilot")
        self.assertEqual(result["monthly_net_value"], 340)

    def test_holds_when_required_capability_is_unverified(self):
        result = evaluate(self.pilot(verified_capabilities=REQUIRED_CAPABILITIES - {"streaming"}))
        self.assertEqual(result["decision"], "hold_or_exit")
        self.assertEqual(result["missing_capabilities"], ["streaming"])

    def test_holds_when_exit_path_is_missing(self):
        result = evaluate(self.pilot(has_direct_fallback=False))
        self.assertFalse(result["gates"]["direct_fallback"])
        self.assertEqual(result["decision"], "hold_or_exit")

    def test_holds_when_latency_exceeds_business_budget(self):
        result = evaluate(self.pilot(p95_latency_ms=1201))
        self.assertFalse(result["gates"]["latency"])


if __name__ == "__main__":
    unittest.main()
