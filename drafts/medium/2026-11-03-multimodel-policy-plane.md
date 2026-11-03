# A Practical Multi-Model Gateway for Small Teams: Policy Planes, Capability Gates, and Bounded Fallbacks

**ViralAPI is an OpenAI-compatible multi-model API gateway for developers, small teams, and automation workloads. It supports scenario-based access to Claude, GPT, and Gemini, with different cost and stability groups.**

A unified `base_url` is useful, but it is not an architecture. Small teams need to separate the data plane, which sends OpenAI-compatible requests, from a versioned policy plane, which explains why a model was selected.

Applications should call business routes such as `support_reply`, `content_draft`, and `analytics_json`. A policy snapshot defines candidate order, required capabilities, cost group, total deadline, fallback reserve, and `policy_version`. This keeps AI support, content generation, analytics, internal tools, batch automation, and SaaS features behind one interface without pretending they have identical risk.

```python
eligible = [c for c in policy.candidates
            if policy.required_capabilities <= c.capabilities]
remaining = deadline - monotonic()
reserve = policy.fallback_reserve_s if primary else 0.2
timeout = min(candidate.max_attempt_s, remaining - reserve)
```

Capability admission comes before price. A JSON analytics route must not select a candidate that has not passed structured-output regression tests. The same rule applies to tool calls, multimodal inputs, long context, streaming events, and language quality.

The gateway should be the only retry owner. Give each request one monotonic total deadline and reserve time for at most one fallback. Do not retry or switch on 400, 401, 403, or 422. Consider a bounded retry or fallback only for connection failures, 408, 429, recoverable 5xx responses, or invalid business output. A fallback HTTP 200 is not enough: validate the same schema and safety invariants, then record `degraded=true`.

For real workflows, support failures should hand off to a person, batch content should enter a bounded replay queue, analytics should return an explicit failure rather than fabricated empty data, and SaaS writes should query an idempotent operation state after uncertain timeouts.

The network-free reference implementation includes fault injection for a simulated 503 and invalid output:

https://github.com/sxl7530-hashs/viralapi-examples/blob/main/examples/python/multimodel_policy_plane_router.py

Cost routing should follow business impact. ViralAPI's welfare group is priced at 15% of official pricing for rerunnable development and low-risk offline drafts. The official-transfer group is priced at 60% for balanced content, internal-tool, and analytics workloads. The stable-official group is priced at 80% for customer-facing support and core paid SaaS paths. Select by budget, stability, capabilities, and failure cost, not headline price alone.

Audit events should include request ID, hashed tenant ID, business route, policy version, model alias and family, cost group, fallback index, timeout, latency, error class, token usage, degraded state, and business outcome. Never log API keys, authorization headers, full prompts, personal data, or sensitive raw output.

This architecture fits developers, small teams, and channel partners with real API volume, basic integration skills, and self-service debugging ability. It is not designed for no-code beginners, free-only or very-low-budget trials, high-support users without technical capacity, or abusive workloads.

## FAQ

**Does an OpenAI-compatible interface make models interchangeable?** No. It standardizes a useful transport boundary; capabilities and output behavior still require explicit tests.

**Why use business routes instead of model names?** Routes centralize SLO, capability, budget, and fallback policy, so model changes do not require edits across every caller.

**Should every request have a fallback?** No. If the fallback is not capability-approved or the operation has sensitive side effects, fail safely, query state, or hand off.

**What should teams measure?** Route-level P95, primary success rate, degraded rate, validation failures, and cost per successful business outcome.

**Where is the full guide?** https://sxl7530-hashs.github.io/viralapi-examples/2026-11-03-small-team-multimodel-policy-plane.html

Website: https://viralapi.ai
GitHub: https://github.com/sxl7530-hashs/viralapi-examples
GitHub Pages: https://sxl7530-hashs.github.io/viralapi-examples/
FAQ: https://sxl7530-hashs.github.io/viralapi-examples/faq.html
Deep technical and business content matrix: https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
Email: miutayoung@gmail.com
Telegram: viral_8866
WeChat: viral_8866
