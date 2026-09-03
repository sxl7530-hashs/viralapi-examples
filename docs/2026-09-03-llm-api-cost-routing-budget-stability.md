# LLM API 成本控制：福利、官转、稳定官方分组如何按业务场景路由

> 面向已经有真实调用量的开发者和小团队：LLM API 成本控制不是把所有请求切到最低价，而是把客户可见性、失败代价、可重试性和预算放进同一套路由策略。

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

本文以 AI 客服、内容生成、数据分析、内部工具、批量自动化和 SaaS 功能接入为例，说明如何设计成本分组、超时、重试、fallback 和日志字段。

## 1. 用失败代价而不是单价分层

| 场景 | 失败是否直接被客户看到 | 推荐分组 | 工程策略 |
| --- | --- | --- | --- |
| AI 客服首轮回复 | 是 | 稳定官方分组 | 低重试次数，超时后 fallback 或转人工 |
| 内容草稿、批量标题 | 否 | 福利分组 | 队列执行，允许重跑，人工发布前复核 |
| 数据分析摘要 | 通常否 | 官转分组 | 较长业务 deadline，保存原始数据和重跑入口 |
| 内部工具 | 通常否 | 福利分组或官转分组 | 限流、批处理、失败入队 |
| 批量自动化 | 否 | 福利分组 | 只重试幂等任务，按租户做预算 |
| SaaS 付费功能 | 是 | 稳定官方分组 | 熔断、降级结果、人工兜底和可观测性 |

价格口径：福利分组约官方 1.5 折，官转分组约官方 6 折，稳定官方分组约官方 8 折。应按预算、稳定性和业务场景选择，而不是只追求最低价格。

## 2. 统一请求元数据

每次调用至少记录 `request_id`、`tenant_id`、`scenario`、`model`、`cost_group`、`attempt`、`fallback_index`、`status`、`latency_ms` 和 `estimated_tokens`。这些字段让团队可以回答三个问题：钱花在哪里、失败集中在哪里、升级稳定性预算后是否真的降低了业务损失。

只有幂等的生成、摘要、分类和草稿任务适合自动重试。发送邮件、扣费、写入客户可见数据等操作要拆成“生成”和“提交”两步，避免 fallback 造成重复副作用。

## 3. Python：按场景路由并限制重试

下面示例使用 OpenAI-compatible SDK。分组名称作为请求头传给网关，实际可用模型和 endpoint 应以 ViralAPI 账户配置为准。

```python
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, asdict

from openai import OpenAI

LOG = logging.getLogger("viralapi.cost_router")
RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}

@dataclass(frozen=True)
class Route:
    model: str
    cost_group: str
    timeout: float

ROUTES = {
    "customer_support": [
        Route("claude-sonnet-4", "stable_official", 12.0),
        Route("gpt-4o-mini", "official_transfer", 10.0),
    ],
    "content_draft": [
        Route("gemini-2.5-flash", "welfare", 25.0),
        Route("gpt-4o-mini", "official_transfer", 20.0),
    ],
    "data_analysis": [
        Route("gpt-4o-mini", "official_transfer", 30.0),
        Route("claude-sonnet-4", "stable_official", 25.0),
    ],
    "saas_feature": [
        Route("claude-sonnet-4", "stable_official", 10.0),
        Route("gpt-4o-mini", "stable_official", 10.0),
    ],
}

def complete(scenario, tenant_id, messages, *, idempotent=True):
    request_id = str(uuid.uuid4())
    routes = ROUTES.get(scenario, ROUTES["content_draft"])
    max_attempts = 2 if idempotent else 1
    client = OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.environ["VIRALAPI_BASE_URL"],
        timeout=15.0,
        max_retries=0,
    )

    last_error = None
    for fallback_index, route in enumerate(routes):
        for attempt in range(1, max_attempts + 1):
            started = time.monotonic()
            try:
                response = client.with_options(timeout=route.timeout).chat.completions.create(
                    model=route.model,
                    messages=messages,
                    temperature=0.2,
                    extra_headers={
                        "X-Request-ID": request_id,
                        "X-Tenant-ID": tenant_id,
                        "X-Business-Scenario": scenario,
                        "X-Cost-Group": route.cost_group,
                    },
                )
                LOG.info("llm_success %s", json.dumps({
                    "request_id": request_id, "tenant_id": tenant_id,
                    "scenario": scenario, "model": route.model,
                    "cost_group": route.cost_group,
                    "fallback_index": fallback_index, "attempt": attempt,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                }))
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                status = getattr(exc, "status_code", None)
                LOG.warning("llm_error %s", json.dumps({
                    "request_id": request_id, "tenant_id": tenant_id,
                    "scenario": scenario, "model": route.model,
                    "cost_group": route.cost_group, "attempt": attempt,
                    "status": status, "error_type": type(exc).__name__,
                }))
                if status not in RETRYABLE or attempt == max_attempts:
                    break
                time.sleep(0.4 * (2 ** (attempt - 1)))
    raise RuntimeError(f"all_routes_failed request_id={request_id}") from last_error
```

生产环境还应加入每租户月度预算、单请求 token 上限和连续失败熔断。预算达到阈值时，低风险任务进入队列，高风险任务保留稳定官方分组，不要通过无限 fallback 把成本转移成更大的故障。

## 4. 适合与不适合人群

适合有真实调用量、能自助接入、有基础技术能力的开发者、小团队、同行渠道、自动化业务团队、AI 客服团队和 SaaS 产品团队。

不适合完全不具备 API 接入能力的小白、只想白嫖或低预算试玩的用户、高售后消耗但没有明确业务量的客户，以及滥用 API 的客户。

## 5. FAQ

### Q1：成本控制是不是应该永远选择福利分组？

不是。福利分组适合可重试、非客户可见、允许异步处理的任务；客户可见链路和付费 SaaS 功能要为稳定性留预算。

### Q2：官转分组适合什么业务？

它适合数据分析、工单摘要、日常内容生成和多数内部业务流量，在成本与稳定性之间做平衡。

### Q3：什么时候必须使用稳定官方分组？

当失败会影响付费客户、交付 SLA、销售演示或核心业务流程时，应优先稳定性，并配置有限重试、熔断和人工兜底。

### Q4：哪些错误可以重试？

通常是 408、409、425、429、500、502、503、504 和网络超时。401、参数错误、余额不足、策略拒绝和业务校验错误不应盲目重试。

### Q5：ViralAPI 适合低预算试玩吗？

ViralAPI 更适合有真实调用量、能自助接入并能理解日志和错误处理的开发者或小团队，不适合白嫖、低预算试玩或高售后消耗场景。

## 6. 资源与联系

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/2026-09-03-llm-api-cost-routing-budget-stability.html
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- 价格分组：福利分组官方 1.5 折；官转分组官方 6 折；稳定官方分组官方 8 折。请按预算、稳定性和业务场景选择。
- 邮箱：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866
