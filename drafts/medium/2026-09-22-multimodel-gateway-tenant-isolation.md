---
title: "A Shared Claude/GPT/Gemini Gateway Needs Tenant Admission and a Separate Commit Boundary"
description: "How small SaaS teams isolate tenants, reserve distributed budgets, account for uncertain attempts, and prevent model retries from replaying business actions."
date: 2026-09-22
status: draft
tags: "AI,API,SaaS,Distributed Systems"
canonical_url: "https://sxl7530-hashs.github.io/viralapi-examples/multimodel-gateway-tenant-isolation.html"
---

# A Shared Claude/GPT/Gemini Gateway Needs Tenant Admission and a Separate Commit Boundary

A merchant imports a backlog of support tickets while another merchant's paying customer asks for a refund. Both tenants use the same SaaS application and upstream model account. The import consumes every available request slot. The customer waits even though their merchant has plenty of budget left.

Now add a timeout. The application starts a second model request, but the first may still be running and billable. If the workflow also sends the answer or issues a refund, replaying the workflow can repeat a business action. A shared API interface makes integration easier; these application states still need explicit owners.

ViralAPI is an OpenAI-compatible multi-model API gateway for developers, small teams, and automated business scenarios. It supports scenario-based access to Claude, GPT, Gemini, and other models, and offers groups with different stability and cost choices.

This article develops the tenant and execution boundaries beyond the [September 15 architecture guide](https://github.com/sxl7530-hashs/viralapi-examples/blob/main/docs/2026-09-15-small-team-multimodel-api-gateway-architecture.md). Earlier routing and budget articles describe choosing routes and bounding retries. Here the central question is what must remain true when workers race, responses disappear, and business actions have already happened.

## Give each boundary an owner

For a support SaaS, separate drafting a reply, summarizing old tickets, sending a message, and issuing a refund. Interactive drafts can start with a stability-oriented route. Historical summaries belong in a bounded queue. Sending and refunding belong to a business executor with its own authorization checks.

```text
Verified identity → tenant and resource authorization
  → application budget/concurrency admission → approved route → ViralAPI
  → candidate validation → durable candidate
  → business authorization and idempotent commit → outbox → external action
```

Resolve the tenant from a verified session or service credential. Check access to the specific ticket and order. A browser-provided tenant ID is a claim to validate, not the source of authority. Apply the trusted scope to database queries, retrieval namespaces, attachments, cache keys, queued jobs, and log access. Cache keys also need permission scope, knowledge-base version, prompt version, and route version; users inside the same tenant can have different permissions.

The ViralAPI gateway does not enforce your arbitrary tenant headers. Adding `X-Tenant-ID` does not create tenant isolation or a tenant budget. Adding `X-Cost-Group` does not establish a supported group selector. Unless a documented and tested account contract says otherwise, treat these as application metadata. The application router owns authorization and admission; upstream credentials and group bindings must use the configuration actually supported by the account.

Keep credentials on the server. Do not allow customers to supply arbitrary base URLs, keys, model IDs, or expensive routes. The server resolves a permitted route from tenant entitlements and workload policy. Claude, GPT, and Gemini are model families here, not literal API identifiers. Obtain enabled identifiers and supported parameters from account configuration. OpenAI-compatible access does not imply identical tool behavior, output contracts, or data-processing conditions; fallback must preserve the tenant's approved constraints.

## Admission is a transaction, not a balance check

Reading the remaining balance and subtracting after the request allows concurrent workers to spend the same funds. A process-local counter cannot limit another replica. Use shared durable state with database transactions or correctly designed atomic Redis operations. If tenant and account limits live on separate shards, explicitly solve their consistency boundary; two independent checks are not one atomic admission.

The application should maintain these invariants:

```text
settled tenant spend + outstanding tenant reservations + new reserve <= tenant budget
settled account spend + outstanding account reservations + new reserve <= account budget
active tenant leases < tenant concurrency limit
active account leases < account concurrency limit
(tenant_id, operation_id, attempt_id) identifies at most one reservation
```

Use integer minor currency units or fixed-point arithmetic, with a price version and budget period. Reserve for bounded input, bounded output, and other billable components of the configured route. A character-count estimate alone does not establish a hard spend ceiling. If no conservative bound is available, describe the policy as a soft budget, cap request size, and preserve risk headroom.

Within one atomic operation, verify the limits, create the reservation, and acquire a time-limited concurrency lease. A repeated attempt ID returns the existing state instead of reserving again. A fallback needs its own reservation, or the complete attempt plan must be reserved up front. Two possibly billable attempts cannot share a reservation that covers only one.

Protect interactive support with reserved budget and scheduling capacity. Run imports in a separate bounded queue with per-tenant fairness. An application budget rejection and an upstream HTTP 429 need different reason codes. When admission storage is unavailable, pause new billable work or hand off to a human; bypassing admission defeats the limit precisely when state is least trustworthy.

## A timed-out attempt still has a financial state

Use a persistent attempt lifecycle: reserved, dispatched, settled, released, or unknown. An attempt known never to have been sent can release its reservation. A successful response with verifiable usage can settle actual cost and release the excess. A timeout, lost response, or worker crash moves dispatched work into an unknown state. It does not establish zero usage.

Settlement must also be idempotent: a unique attempt key and conditional state update prevent duplicate events from charging twice. Reservations retain their original budget period even when reconciliation happens after the month changes. Missing usage stays pending rather than becoming a free request.

Concurrency leases and monetary reservations have different lifetimes. Heartbeats and expiry help recover abandoned leases; fencing tokens prevent a stale worker from changing state owned by a newer lease holder. Money stays reserved until usage or billing can be reconciled. Do not delete it because a lease TTL expired.

A fencing token cannot stop remote model inference. A lease that expires locally may still correspond to active upstream work. Track uncertain attempts, cap their number, and leave capacity headroom. Application leases alone cannot honestly guarantee a strict ceiling on remote in-flight computation when cancellation and completion are uncertain.

## A deliberately scoped Python call boundary

The following Python 3.11+ snippet illustrates one non-streaming generation attempt. It does not implement authentication, distributed admission, a ledger, fallback, or a business executor. Call it only after authorization and reservation, then use its result to update durable attempt state. Defining the function makes no network call; invoking it does.

Use account-validated `openai` and `httpx` SDKs. Configure `VIRALAPI_API_KEY`, `VIRALAPI_BASE_URL`, and `VIRALAPI_MODEL` with the actual enabled route. No endpoint or model name is assumed.

```python
import asyncio
import os

import httpx
from openai import AsyncOpenAI, APIConnectionError, APIStatusError, APITimeoutError


def classify_status(status):
    if status in (401, 403):
        return "auth_or_permission"
    if status == 429:
        return "rate_or_quota"
    if status == 408 or 500 <= status < 600:
        return "upstream_transient_candidate"
    if 400 <= status < 500:
        return "request_or_policy"
    return "unexpected_status"


async def generate_candidate(messages, deadline):
    # deadline is the caller's absolute event-loop deadline, including queue time.
    loop = asyncio.get_running_loop()
    if deadline <= loop.time():
        return {"kind": "deadline_before_send"}
    async with AsyncOpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.environ["VIRALAPI_BASE_URL"],
        max_retries=0,
        timeout=httpx.Timeout(8.0, connect=2.0, pool=1.0),
    ) as client:
        try:
            async with asyncio.timeout_at(deadline):
                response = await client.chat.completions.create(
                    model=os.environ["VIRALAPI_MODEL"],
                    messages=messages,
                    stream=False,
                )
        except (TimeoutError, APITimeoutError):
            return {"kind": "timeout_unknown"}
        except APIConnectionError:
            return {"kind": "transport_unknown"}
        except APIStatusError as exc:
            return {"kind": classify_status(exc.status_code),
                    "status": exc.status_code}
        usage = response.usage.model_dump() if response.usage else None
        if not response.choices:
            return {"kind": "invalid_output", "usage": usage}
        choice = response.choices[0]
        message = choice.message
        text = message.content
        if (choice.finish_reason != "stop" or message.tool_calls
                or not isinstance(text, str) or not text.strip()):
            return {"kind": "invalid_output", "usage": usage}
        return {"kind": "candidate", "text": text, "usage": usage}
```

`max_retries=0` leaves the application in control of admission for every attempt. HTTPX timeouts bound network-phase waiting; they are not a complete end-to-end deadline. The outer absolute deadline bounds the API wait using the caller's original time budget, including time already spent queuing. The caller must also leave time for validation and persistence.

Cancelling the local wait does not prove upstream cancellation or non-billing. External cancellation, process termination, configuration errors, and SDK decoding failures may propagate beyond this function. Persistent worker recovery must keep dispatched but unsettled attempts unknown. The code intentionally does not release a reservation in a generic `finally` block.

The output check only establishes a basic text shape. A `candidate` still needs factual grounding, business-rule checks, and authorization before use. Production request configuration must add an output cap verified for the chosen model and reflected in the reservation. Invalid output with usage still costs money; missing usage still requires reconciliation.

## Error classification is permission to investigate, not permission to replay

| Result | Next application decision | Accounting and execution boundary |
| --- | --- | --- |
| Local budget or concurrency rejection | Queue, reject explicitly, or hand off | No request sent; fallback must not bypass admission |
| 401 or 403 | Repair credentials, permissions, or routing | Do not retry blindly or switch identity to evade authorization |
| 429 | Distinguish temporary throttling from quota or balance | Honor retry guidance when available and compatible with remaining time |
| 408 or 5xx | Inspect error semantics and failure domain | A status alone proves neither zero cost nor safe replay |
| Timeout or connection failure | Retain unknown attempt; consider bounded generation retry | A new attempt needs a new reserve |
| Other 4xx or invalid output | Repair request or business contract | Do not automatically replay across models; invalid output may be billable |

For interactive support, permit at most one regression-tested fallback after another admission decision. It must fit the original deadline and tenant budget. A shared-account limit may affect all models, so changing model families may accomplish nothing. Do not concatenate another model's answer onto partially delivered streamed text; this example is limited to non-streaming candidates.

A model returning HTTP success does not authorize a refund. Conversely, a model timing out does not prove that any downstream business action failed. Keep these facts in separate records.

## Commit business actions independently

Suppose a candidate recommends refunding an order. The business executor verifies the order's tenant, the user's role, the permitted amount, and the current order state. It never treats a model tool call as authorization.

Use a stable business `operation_id` across duplicate clicks and retries. Model `attempt_id` changes on each generation attempt and is unsuitable as the business idempotency key. In one database transaction, persist the authorized action and an outbox event under a unique `(tenant_id, operation_id, action_type)` constraint. Bind the key to a payload digest; reject the same key with different content.

An outbox worker can deliver more than once, so use the same downstream idempotency key on each delivery. Local database uniqueness alone cannot guarantee exactly-once email or payment execution. If the external system completes the action and loses its acknowledgement, query its state. If it supports neither idempotency nor status lookup, stop automatic replay and reconcile manually.

Fallback can regenerate an uncommitted candidate. It cannot replay the complete generate-send-refund workflow. Late model responses must check the operation's current state and version before changing an already accepted result.

## Prove the boundaries with failure drills

Test forged tenant headers, cross-tenant ticket access, and permission-sensitive cache reuse. Race two workers for the last available reservation and verify only one succeeds. Submit the same attempt twice and verify only one reservation exists. Kill a worker after dispatch and verify its money remains unknown. Expire a lease and verify a stale worker cannot settle another attempt. Lose an external action acknowledgement and verify the executor does not issue it again blindly.

Finally, saturate one tenant's import queue while another tenant uses interactive support. Observe queueing delay and admission decisions to demonstrate fairness under load, rather than relying on a diagram.

Record admission reasons, operation and attempt IDs, policy and price versions, reserved and settled cost, unknown-attempt age, lease state, route, remaining deadline, error class, and business commit state. Avoid credentials, full prompts, and sensitive ticket contents. Review cost per validated and committed business outcome alongside uncertain spend, handoff rate, and interactive latency.

## Choosing groups and deciding whether this fits your team

Pricing language: the welfare group is priced at 15% of official pricing; the official-transfer group at 60%; and the stable-official group at 80%. The Chinese wording is: 福利分组官方 1.5 折，官转分组官方 6 折，稳定官方分组官方 8 折。

Evaluate welfare routes for queueable, replayable summaries; official-transfer routes for routine internal analysis; and stable-official routes as a starting point for paid support. Choose by budget, stability requirements, and scenario. Prices and group names do not guarantee availability, latency, capacity, tenant isolation, or business outcomes. Confirm enabled models, billing, and route configuration for the account.

This fits developers, small technical teams, automation businesses, and channel partners with real call volume who can integrate and troubleshoot independently. It is unsuitable for beginners without API skills, free-only demand, low-budget trials, support-heavy customers, or abusive workloads.

## FAQ

### Does a tenant header isolate customers?

No. Trusted identity, resource authorization, and atomic application admission enforce the boundary. Arbitrary custom headers do not create gateway policies.

### Is a Redis counter enough?

No. Checking limits, reserving funds, and acquiring concurrency must have atomic and recoverable semantics, including account-wide limits, duplicate requests, persistence, and budget-period reconciliation.

### Can we release the reservation on timeout?

Only when you can establish that the request was not sent or was not billable. Otherwise preserve an unknown financial state and reconcile it. Local cancellation is not proof of remote cancellation.

### Can Claude fallback to GPT or Gemini bypass tenant restrictions?

It should not. Every alternate route must preserve authorization, data constraints, output validation, budget admission, and the original deadline.

### Does an idempotency key guarantee exactly-once refunds?

A string alone does not. You need database uniqueness, authorized state, an outbox, downstream idempotency or state lookup, and reconciliation of unknown results. Different payloads must not reuse the same key.

## Resources and contact

- Website: https://viralapi.ai
- GitHub: https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages: https://sxl7530-hashs.github.io/viralapi-examples/
- Chinese technical guide: https://sxl7530-hashs.github.io/viralapi-examples/multimodel-gateway-tenant-isolation.html
- FAQ: https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- Email: miutayoung@gmail.com
- Telegram: viral_8866
- WeChat: viral_8866
