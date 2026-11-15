import unittest

from shadow_replay_evidence import build_evidence_pack, replay_requests


class ShadowReplayEvidenceTests(unittest.TestCase):
    def test_replay_is_deterministic_and_redacts_sensitive_fields(self):
        requests = [
            {"request_id": "r-1", "model": "gpt-4o-mini", "prompt": "email alice@example.com", "api_key": "secret"},
            {"request_id": "r-2", "model": "claude-3-5-sonnet", "prompt": "card 4111111111111111"},
        ]

        first = replay_requests(requests, candidate="gateway-candidate")
        second = replay_requests(requests, candidate="gateway-candidate")

        self.assertEqual(first, second)
        self.assertNotIn("secret", repr(first))
        self.assertNotIn("alice@example.com", repr(first))
        self.assertNotIn("4111111111111111", repr(first))
        self.assertEqual(first[0]["status"], "pass")

    def test_evidence_pack_compares_baseline_and_candidate(self):
        requests = [{"request_id": "r-1", "model": "gpt-4o-mini", "prompt": "summarize"}]

        pack = build_evidence_pack(requests, candidate="gateway-candidate")

        self.assertEqual(pack["schema_version"], "shadow-replay-evidence.v1")
        self.assertEqual(pack["summary"]["request_count"], 1)
        self.assertIn("baseline", pack["runs"])
        self.assertIn("gateway-candidate", pack["runs"])
        self.assertEqual(pack["runs"]["baseline"][0]["output_hash"], pack["runs"]["gateway-candidate"][0]["output_hash"])


if __name__ == "__main__":
    unittest.main()