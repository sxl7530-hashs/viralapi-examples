# Production OpenAI-compatible SDK calls for a support SaaS

ViralAPI is an OpenAI-compatible multi-model API gateway for developers, small teams and automation workloads, providing access to models such as Claude, GPT and Gemini with cost and stability groups selected for each scenario.

A support SaaS produces drafts from an authorized knowledge base. A successful HTTP response is only the beginning: tenant permissions, factual grounding and output structure must pass business validation before a customer sees the answer. This example does not send email or execute refunds.

## Admission comes before retries

A semaphore bounds active calls, not queue size or RPM/TPM. Reject excess interactive traffic using a bounded ingress queue; defer batch jobs. Multiple service replicas need shared tenant quota and token budgets. Reuse one SDK client per worker and account for the sum of connection pools across workers.

## One deadline and one retry owner

The runnable Python 3.11+ example below disables SDK retries and places an outer deadline around both semaphore acquisition and the API call. Read base URL and authorized model IDs from the console; never invent them. Configure secrets on the server. Install openai and httpx and pin the versions you actually test.

```python
import asyncio
import json
import os
import time
import uuid

import httpx
from openai import AsyncOpenAI, APIStatusError, APIConnectionError

async def draft_reply(client, slots, messages, model):
    request_id = uuid.uuid4().hex
    started = time.monotonic()
    event = {"request_id": request_id, "scenario": "support_draft",
             "model": model, "attempt": 1, "policy_version": "sdk-v1"}
    try:
        # 覆盖本函数内的排队与调用；入口自己的排队也要纳入入口 deadline。
        async with asyncio.timeout(8):
            async with slots:
                result = await client.chat.completions.create(
                    model=model, messages=messages, max_tokens=256)
        if not result.choices:
            raise ValueError("missing_choices")
        choice = result.choices[0]
        text = choice.message.content
        if choice.finish_reason != "stop" or not text or not text.strip():
            raise ValueError("output_requires_review")
        event["outcome"] = "generated_pending_business_validation"
        # 应用继续做权限、知识库引用、事实与禁词校验，再展示给客户。
        return text
    except TimeoutError:
        event.update(outcome="deadline_exceeded", billing_state="unknown")
        raise
    except APIStatusError as exc:
        event.update(outcome="http_error", status=exc.status_code)
        # 仅选择性记录响应头 request ID；不打印异常正文或全部响应头。
        upstream_id = exc.response.headers.get("x-request-id", "")
        event["upstream_request_id"] = upstream_id[:128]
        raise
    except APIConnectionError:
        event.update(outcome="transport_error", billing_state="unknown")
        raise
    except ValueError:
        event["outcome"] = "output_requires_review"
        raise
    except asyncio.CancelledError:
        event.update(outcome="cancelled", billing_state="unknown")
        raise
    finally:
        event["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        print(json.dumps(event, ensure_ascii=False))

async def main():
    transport = httpx.AsyncClient(
        timeout=httpx.Timeout(5, connect=2, pool=1),
        limits=httpx.Limits(max_connections=8, max_keepalive_connections=8))
    async with AsyncOpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.environ["VIRALAPI_BASE_URL"],
        max_retries=0, timeout=httpx.Timeout(5, connect=2, pool=1),
        http_client=transport) as client:
        slots = asyncio.Semaphore(8)  # 实际服务应在生命周期内共享它。
        messages = [{"role": "user", "content": "Draft a short greeting for a support agent."}]
        await draft_reply(client, slots, messages, os.environ["VIRALAPI_MODEL"])

if __name__ == "__main__":
    asyncio.run(main())

```

The eight-second limit is an illustrative application policy, not an SLA. Cancellation is cooperative and cannot prove that upstream execution or billing stopped. Never replay a financial or messaging action merely because its client timed out. Keep unknown usage separate from zero usage.

Retry 502/503/504 only when the task is replayable, the total deadline allows another attempt and the budget permits it. For 429, distinguish quota exhaustion from congestion; parse Retry-After and reject waits beyond the remaining deadline. Fix 400/401/403 at their source. An evaluated fallback consumes an additional attempt and cost budget; a cheaper model is not automatically an acceptable substitute. Shared upstream quotas do not provide independent failover.

## Operational evidence and cost routing

Record request ID, pseudonymous tenant ID, scenario, model, group, policy version, attempt, queue time, elapsed time, status and usage availability. Do not log API keys, prompts, customer text or raw exception bodies. The minimal example emits only part of this production event schema. Test queue timeout, cancellation, 401, 429, 503, empty choices and truncated output before gradual tenant rollout.

The benefit group is priced at 15% of official pricing, the transferred-official group at 60%, and the stable-official group at 80%. Evaluate budget, stability and business acceptance together; confirm current model access and billing in the console. Batch drafts with human review can evaluate the benefit group, internal analysis can evaluate the transferred-official group, and customer-facing core flows should evaluate the stable-official group. Group choice is routing configuration, not a fabricated OpenAI request parameter. Pricing alone is not an SLA.

## Who this fits

Developers, small teams, SaaS builders and channel partners with real usage and the ability to integrate independently. It is unsuitable for beginners needing extensive support, free-use seekers, low-budget trial traffic, abusive workloads or teams expecting a managed operations service. Verify contractual and data residency requirements before adoption.

## FAQ

### Does a semaphore enforce token limits?
No. Enforce RPM/TPM and tenant budgets separately, across replicas.

### Why disable SDK retries?
To make a single business layer own the deadline, attempt count and spend budget.

### Does cancellation eliminate charges?
No. Reconcile uncertain execution and usage with upstream records.

### Should every error trigger fallback?
No. Repair invalid input and authorization failures; evaluate alternative models against the same acceptance criteria.

### What is the Node.js equivalent?
Reuse the client, set maxRetries to zero, propagate an AbortSignal and bound the admission queue. Promise.race alone does not cancel network execution.

Website: https://viralapi.ai
GitHub: https://github.com/sxl7530-hashs/viralapi-examples
Pages: https://sxl7530-hashs.github.io/viralapi-examples/
FAQ: https://sxl7530-hashs.github.io/viralapi-examples/faq.html
Content matrix: https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
Email: miutayoung@gmail.com; Telegram: viral_8866; WeChat: viral_8866.

Canonical URL: https://sxl7530-hashs.github.io/viralapi-examples/2026-11-06-python-sdk-backpressure-deadline.html
