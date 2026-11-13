import unittest
from datetime import datetime, timezone

from tenant_adaptive_admission_2026_11_13 import (
    TenantAdmission,
    parse_retry_after_ms,
)


class RetryAfterTests(unittest.TestCase):
    def test_parses_delta_seconds(self):
        self.assertEqual(parse_retry_after_ms("2.5"), 2500)

    def test_parses_http_date(self):
        now = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(
            parse_retry_after_ms("Fri, 13 Nov 2026 10:00:03 GMT", now=now),
            3000,
        )

    def test_clamps_past_date_to_zero(self):
        now = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(
            parse_retry_after_ms("Fri, 13 Nov 2026 09:59:59 GMT", now=now), 0
        )

    def test_invalid_or_missing_value_is_unknown(self):
        self.assertIsNone(parse_retry_after_ms(None))
        self.assertIsNone(parse_retry_after_ms("not-a-delay"))
        self.assertIsNone(parse_retry_after_ms("inf"))
        self.assertIsNone(parse_retry_after_ms("nan"))


class TenantAdmissionTests(unittest.TestCase):
    def test_tenant_capacity_is_isolated(self):
        admission = TenantAdmission(initial_limit=2, min_limit=1, max_limit=4)
        self.assertTrue(admission.acquire("tenant-a"))
        self.assertTrue(admission.acquire("tenant-a"))
        self.assertFalse(admission.acquire("tenant-a"))
        self.assertTrue(admission.acquire("tenant-b"))

    def test_success_addsitive_increase_is_bounded(self):
        admission = TenantAdmission(initial_limit=2, min_limit=1, max_limit=3)
        admission.record_success("tenant-a")
        admission.record_success("tenant-a")
        self.assertEqual(admission.limit("tenant-a"), 3)

    def test_throttling_multiplicatively_decreases_only_that_tenant(self):
        admission = TenantAdmission(initial_limit=4, min_limit=1, max_limit=8)
        admission.record_throttle("tenant-a", retry_after_ms=500)
        self.assertEqual(admission.limit("tenant-a"), 2)
        self.assertEqual(admission.limit("tenant-b"), 4)
        self.assertEqual(admission.cooldown_ms("tenant-a"), 500)

    def test_release_never_makes_in_flight_negative(self):
        admission = TenantAdmission(initial_limit=1)
        admission.release("unknown")
        self.assertEqual(admission.in_flight("unknown"), 0)


if __name__ == "__main__":
    unittest.main()