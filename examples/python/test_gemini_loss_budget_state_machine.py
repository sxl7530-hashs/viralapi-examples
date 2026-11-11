"""Offline checks for the Gemini loss-budget decision policy."""
import unittest

from gemini_loss_budget_state_machine import Decision, Observation, POLICIES, decide


class LossBudgetTests(unittest.TestCase):
    def setUp(self):
        self.policy = POLICIES["support"]

    def check(self, decision, **kwargs):
        return self.assertEqual(decide(self.policy, Observation(**kwargs)).decision, decision)

    def test_non_read_only_never_replays(self):
        for status in (None, 200, 400, 429, 500, 503, 599):
            for valid in (None, False, True):
                with self.subTest(status=status, valid=valid):
                    result = decide(self.policy, Observation(status, 10000, read_only=False, output_valid=valid))
                    self.assertNotIn(result.decision, (Decision.RETRY, Decision.FALLBACK, Decision.QUEUE))
                    if status != 200 or valid is False:
                        self.assertEqual(result.decision, Decision.RECONCILE)

    def test_started_response_always_reconciles(self):
        for status in (None, 200, 401, 503):
            with self.subTest(status=status):
                self.check(Decision.RECONCILE, status=status, remaining_ms=10000, response_started=True)

    def test_success_and_invalid_output(self):
        for valid in (None, True):
            self.check(Decision.VALIDATE, status=200, remaining_ms=0, output_valid=valid)
        self.check(Decision.FALLBACK, status=200, remaining_ms=3200, output_valid=False)
        self.check(Decision.STOP, status=200, remaining_ms=3199, output_valid=False)
        result = decide(POLICIES["extraction"], Observation(200, 0, output_valid=False))
        self.assertEqual(result.decision, Decision.QUEUE)

    def test_retry_budget_and_limits(self):
        result = decide(self.policy, Observation(429, 3700, retry_after_ms=500))
        self.assertEqual((result.decision, result.minimum_budget_ms), (Decision.RETRY, 3700))
        self.check(Decision.FALLBACK, status=429, remaining_ms=3699, retry_after_ms=500)
        self.check(Decision.FALLBACK, status=503, remaining_ms=3200, attempts=2)
        self.check(Decision.FALLBACK, status=503, remaining_ms=3200, breaker_open=True)
        self.check(Decision.STOP, status=503, remaining_ms=3199)
        self.check(Decision.STOP, status=503, remaining_ms=3200, breaker_open=True, fallback_compatible=False)

    def test_terminal_and_unknown_statuses(self):
        for status in (400, 401, 403, 404, 422, 418):
            with self.subTest(status=status):
                self.check(Decision.STOP, status=status, remaining_ms=10000)
        self.check(Decision.RECONCILE, status=None, remaining_ms=10000)

    def test_invalid_inputs(self):
        for kwargs in ({"remaining_ms": -1}, {"remaining_ms": 1, "attempts": 0},
                       {"remaining_ms": 1, "retry_after_ms": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                decide(self.policy, Observation(status=503, **kwargs))


if __name__ == "__main__":
    unittest.main()
