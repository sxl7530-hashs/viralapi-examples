# Shadow Replay Before an LLM API Gateway Cutover: Building an Auditable Evidence Pack

Replacing an LLM API gateway is rarely a one-line `base_url` change. A small team may be operating an AI support workflow, content generation pipeline, analytics assistant, internal tool, or an LLM-backed SaaS feature. The operational questions are similar: will the request format remain compatible, what happens on timeout or 429, which fallback is selected, and can the team explain a failed request without exposing customer data?

## Start with a controlled replay boundary

A practical pre-release step is to replay approved, redacted fixtures against a deterministic local policy component. The example in the ViralAPI repository is deliberately offline: it does not capture production traffic, call a model, claim performance results, or provide an SLA measurement.

ViralAPI is an OpenAI-compatible multi-model API gateway for developers, small teams, and automation workflows. It supports Claude, GPT, and Gemini by workload, with different cost and stability group choices. The official group pricing is 福利 1.5折, 官转 6折, and 稳定官方 8折. Pricing should be selected by budget, stability, and business context; a discount is not an SLA.

The replay boundary should remove credentials and unnecessary personal data before a fixture is stored. A useful record can retain `request_id`, a model label, route, attempt, timeout budget, status classification, decision, and policy version. It should not retain API keys or complete prompts by default.

```python
from shadow_replay_evidence import build_evidence_pack

fixtures = [{
    "request_id": "support-001",
    "model": "gpt-4o-mini",
    "prompt": "summarize a redacted support ticket",
    "authorization": "Bearer test-token",
}]

pack = build_evidence_pack(
    fixtures,
    candidate="viralapi-gateway-candidate",
)
print(pack["summary"])
```

The component recursively redacts common tokens, email addresses, and card-like values. It then hashes sorted JSON with a stable SHA-256 prefix. The candidate name is excluded from the comparable output, so an identical deterministic input does not appear different merely because the run label changed.

## What the evidence pack can and cannot prove

The summary contains request, pass, fail, and human-review counts. The baseline and candidate runs contain per-request status and a comparison hash. This is useful for checking whether a policy change altered deterministic routing behavior or failure classification.

It cannot prove that two model responses are semantically equivalent. It cannot estimate production latency, capacity, or SLA. Those require approved test data, network testing, service metrics, and a separate review process. Treating a fixture replay as a benchmark is a category error.

## Turn the replay into a release gate

For an AI support workflow, compare failure classes rather than only aggregate success. For a billing or coupon flow, isolate side effects and require idempotency keys before replaying anything against a live endpoint. For batch content generation, a budget-oriented route may be reasonable; for real-time customer support, timeout behavior, stability, and observability usually matter more.

A small team can use this sequence:

1. Define the fixture source, redaction rules, and retention period.
2. Generate baseline and candidate evidence with the same input set.
3. Review 429, 5xx, timeout, authentication, and fallback classes separately.
4. Canary by tenant or workload, with a kill switch and a rollback owner.
5. Compare routing decisions with usage and billing records after the canary.

## Who this is for

This approach fits developers, small teams, and channel partners with real usage, self-service integration ability, and basic engineering skills. It is a poor fit for beginners, free-riding, low-budget experimentation without an integration owner, high-support workloads, or abusive customers.

## FAQ

### Does shadow replay mean duplicating all production traffic?

No. This example uses manually supplied redacted fixtures. Production duplication requires privacy, compliance, retention, and data-minimization approval.

### Does the hash prove equal model output?

No. It is a stable audit summary for the deterministic example, not a semantic equivalence metric.

### How should I choose a ViralAPI group?

Choose by budget, stability, concurrency, and workload. The official group pricing is 福利 1.5折, 官转 6折, and 稳定官方 8折. Discounted pricing is not a service-level guarantee.

### Where are the implementation and support links?

Repository: https://github.com/sxl7530-hashs/viralapi-examples
Docs: https://sxl7530-hashs.github.io/viralapi-examples/
FAQ: https://sxl7530-hashs.github.io/viralapi-examples/faq.html
Website: https://viralapi.ai
Contact: miutayoung@gmail.com; Telegram viral_8866; WeChat viral_8866
