# Python OpenAI SDK 接入 Claude/GPT/Gemini：SaaS 客服的并发限流、超时与日志实战

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

一个客服 SaaS 接入模型后，开发环境只有一个请求，线上却可能有多个租户同时提交工单。真正需要解决的是：等待连接的任务有没有上限，客户离开后调用是否继续，429 会不会被多层重试放大，以及日志能否解释一次失败。本文给出 Python 异步 SDK 的最小生产边界。场景是对已授权知识库内容生成客服回复草稿，由业务校验后展示；示例不执行退款、发信等外部操作，也不代表线上压测结果。

## 1. 先约定三个互不替代的限制

队列长度限制控制内存；并发限制控制正在调用的数量；速率限制控制单位时间的请求数与 token 数。Semaphore 只覆盖第二项，不能宣称实现平台 RPM/TPM 限流。HTTP 连接池的上限也不是业务队列上限。

客服 HTTP 入口应使用有界队列并拒绝超额流量，返回可解释的忙碌状态。批量内容生成则可以推迟任务。多副本部署需要共享的租户配额与预算账本，进程内限流不能保证集群总量。连接池按 worker 数量计算总连接数，避免每个请求创建一个新 client。

## 2. 环境变量与 SDK 生命周期

仅在服务器设置 VIRALAPI_API_KEY、VIRALAPI_BASE_URL、VIRALAPI_MODEL。base URL 和实际有权限的模型 ID 从控制台获取，不猜测模型名称或 API 路径。依赖为 openai 与 httpx；Python 3.11+ 提供 asyncio.timeout。上线时锁定实际回归过的依赖版本。

```bash
python -m pip install openai httpx
# 在服务器的 secret 管理中设置环境变量，不在前端或仓库内填写真实密钥。
python -c 'import os; assert all(os.getenv(k) for k in ("VIRALAPI_API_KEY", "VIRALAPI_BASE_URL", "VIRALAPI_MODEL")); print("config present")'
```

下面是可独立运行的单次请求示例。为了让超时覆盖等待 semaphore 的时间，外层 deadline 必须放在 acquire 之前。SDK 内部自动重试关闭，由业务层决定是否重试。此例不自动重放请求。

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

这里的 8 秒是应用示例阈值，不是 SLA。asyncio.timeout 通过协作式取消工作；同步阻塞、CPU 密集解析或忽略取消的组件会延迟退出。它不保证上游停止执行或停止计费。SDK 的读超时也不等于整个业务流程的墙钟截止。外层服务需覆盖入口排队、调用、验证和响应写回，并监控实际耗时。

## 3. 重试策略必须有业务上下文

401/403 先核对权限与授权，400 先修正参数，不更换模型掩盖错误。429 区分租户余额、共享配额与短期拥塞；读取 Retry-After，支持秒数和 HTTP 日期格式，若等待超过剩余 deadline，则直接排队或降级为人工处理。

对 502/503/504 或暂时连接失败，只在任务可重放、预算允许、总 deadline 足够时增加至多一次额外尝试。不能同时打开 SDK、反向代理和业务代码的自动重试。一次 fallback 也占用额外尝试与预算。超时后的工具执行状态必须先查证，不能直接重放退款或发信。

本示例刻意没有自动 fallback：客服回复必须保持知识库权限、语气与结构验收一致。评估过的 Claude/GPT/Gemini 备用模型可以接入业务路由器；未验证的模型不应临时替代核心链路。主备共享上游额度时，不构成独立容灾。

## 4. 日志、成本与分组如何衔接

日志记录 request_id、脱敏 tenant_id、scenario、model、cost_group、policy_version、attempt、queue_ms、elapsed_ms、status、upstream_request_id、validation_passed 与 usage 可用状态。示例打印的是基础事件；真实服务需要 JSON 日志采集、队列耗时与账单对账，不记录完整提示词、客户数据、密钥或异常响应正文。HTTP 200 只证明收到响应，不证明业务验收合格。

福利分组官方 1.5 折，官转分组官方 6 折，稳定官方分组官方 8 折。按预算、稳定性、业务场景选择：可重放且有复核的批量内容可评估福利分组；内部工具与数据分析可评估官转分组；客户可见客服和付费 SaaS 核心链路优先评估稳定官方分组。具体模型、权限和计费以控制台为准，价格不是 SLA。

分组由控制台和已有路由配置选择，不把自创 group 字段塞入 OpenAI 请求。对比各路线时统计验收通过率、P95 延迟、排队拒绝率以及每份合格结果的总费用，包含重试和人工复核。未知 usage 不作为零成本。

## 5. 上线前做故障演练

使用假 transport 或测试服务分别模拟：慢响应、排队超时、429 长 Retry-After、401、503、空 choices、截断输出、客户端断开。验收标准是队列有界、每次调用只由一层控制重试、取消后 semaphore 释放、日志不泄露内容、主备都失败时转人工。上线灰度按租户与场景放量，保留策略版本和回滚入口。

Node.js 接入也应在服务生命周期内共享 client，设置 maxRetries: 0，传递入口 AbortSignal，并用带上限的队列管理并发。AbortSignal 与 Python 取消具有同样的计费不确定性；不要把 Promise.race 超时当成取消网络调用。

## 适合与不适合

适合有真实调用量、能自助接入、有基础技术能力的小团队、开发者、SaaS 团队和同行渠道。不适合小白、白嫖、低预算试玩、高售后消耗或滥用客户。需要合同 SLA、特定数据驻留或监管保证的业务，应先核实相应条件，再决定接入路线。

## FAQ

### 1. SDK 的 timeout 已设置，为什么还会排队很久？

业务队列和 semaphore 等待可能在 SDK 调用之前发生。把整个请求的等待纳入入口 deadline，并限制队列长度。

### 2. Semaphore 能解决 RPM/TPM 配额吗？

不能。并发与速率是不同约束；多副本还需要共享租户配额和 token 预算。

### 3. 为什么关闭 SDK 重试？

为了让业务层统一控制尝试次数、Retry-After、预算与截止时间。并不意味着所有请求都应该重试。

### 4. 取消调用就不会计费吗？

不能这样假设。上游可能已执行；记录未知状态并对账。

### 5. 能否直接把所有失败切到更便宜模型？

不能。授权与参数错误应修复；备用路线必须通过同一业务验收，费用与时间预算也必须允许。

官网：https://viralapi.ai

GitHub：https://github.com/sxl7530-hashs/viralapi-examples

GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/

FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html

深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html

邮箱：miutayoung@gmail.com；Telegram：viral_8866；WeChat：viral_8866。
