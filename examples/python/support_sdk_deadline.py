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
