"""Offline deterministic shadow replay and release evidence example.

This module does not capture traffic or call a model. It turns supplied request
fixtures into comparable, redacted records for a human review gate.
"""

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List


_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_API_KEY = re.compile(r"\b(?:sk|key|token)[_-]?[A-Za-z0-9_-]{8,}\b", re.IGNORECASE)
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_SENSITIVE_KEYS = {"api_key", "apikey", "authorization", "access_token", "token", "secret"}


def redact(value: Any) -> Any:
    """Recursively redact common credentials and personal identifiers."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in _SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = _EMAIL.sub("[REDACTED_EMAIL]", value)
        value = _API_KEY.sub("[REDACTED_TOKEN]", value)
        return _CARD.sub("[REDACTED_CARD]", value)
    return value


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def replay_requests(requests: Iterable[Dict[str, Any]], candidate: str = "candidate") -> List[Dict[str, Any]]:
    """Create deterministic comparison records from sanitized request fixtures."""
    records = []
    for request in requests:
        safe_request = redact(request)
        request_id = str(safe_request.get("request_id", _stable_hash(safe_request)))
        output = {
            "request_id": request_id,
            "model": safe_request.get("model", "unspecified"),
            "candidate": candidate,
            "decision": "review",
            "status": "pass" if safe_request.get("prompt") else "fail",
        }
        comparable_output = {key: value for key, value in output.items() if key != "candidate"}
        records.append({**output, "output_hash": _stable_hash(comparable_output)})
    return records


def build_evidence_pack(requests: Iterable[Dict[str, Any]], candidate: str = "candidate") -> Dict[str, Any]:
    """Build a JSON-serializable baseline/candidate evidence package."""
    request_list = list(requests)
    baseline = replay_requests(request_list, candidate="baseline")
    candidate_records = replay_requests(request_list, candidate=candidate)
    total = len(candidate_records)
    passed = sum(record["status"] == "pass" for record in candidate_records)
    return {
        "schema_version": "shadow-replay-evidence.v1",
        "scope": "offline_fixture_replay",
        "summary": {
            "request_count": total,
            "pass_count": passed,
            "fail_count": total - passed,
            "human_review": total > 0,
        },
        "runs": {"baseline": baseline, candidate: candidate_records},
    }


if __name__ == "__main__":
    fixture = [{"request_id": "demo-1", "model": "gpt-4o-mini", "prompt": "summarize a release note"}]
    print(json.dumps(build_evidence_pack(fixture, "gateway-candidate"), indent=2))