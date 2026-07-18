# LLM API 成本控制实战：按业务场景路由福利、官转、稳定官方分组

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

很多团队讨论 LLM API 成本时，只看单次调用价格，但真正上线后，成本通常来自三件事：模型选型不分场景、失败重试没有边界、以及所有业务都走同一条高稳定链路。对 AI 客服、内容生成、数据分析、内部工具、批量自动化和 SaaS 功能接入来说，更合理的做法是把成本控制做成一层可观测的业务路由，而不是临时把模型改成“更便宜的那个”。

- 官网：https://viralapi.ai
- GitHub 仓库：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html

## 一、真实业务场景：同一个系统里存在三类成本目标

以一个小型 SaaS 或自动化团队为例，常见调用可以拆成三类：

1. **收入相关链路**：AI 客服、付费用户的应用内 AI 功能、销售线索分析。这里失败会直接影响转化或留存，稳定性优先。
2. **批量生产链路**：SEO/GEO 内容草稿、商品描述、邮件初稿、客服知识库改写。这里吞吐和成本更重要，允许异步重试。
3. **内部效率链路**：运营报表摘要、工单分类、会议纪要、数据分析辅助。这里需要稳定，但通常可以接受更长延迟。

如果这三类请求都使用同一个模型、同一个分组、同一个 timeout 和 retry 策略，结果通常是：关键链路不够稳，批量链路又太贵，排障时也不知道到底是哪类业务在消耗预算。

ViralAPI 的价格分组应按预算、稳定性和业务场景选择：福利分组约官方 **1.5 折**，适合成本敏感、可异步重试的批量任务；官转分组约官方 **6 折**，适合平衡成本和稳定性的持续业务；稳定官方分组约官方 **8 折**，适合 AI 客服、核心 SaaS 功能和高价值请求。表达重点不是低价薅羊毛，而是把不同稳定性需求的流量放到合适链路上。

## 二、成本路由的基本设计

推荐先在服务端定义一个统一入口，让业务代码只传 `scenario`、`user_tier`、`messages` 和 `request_id`。路由层负责决定：

- 主模型和 fallback 模型；
- 使用哪个成本/稳定性分组；
- timeout 和 retry 次数；
- 是否允许降级到更便宜或更快的模型；
- 日志字段和预算归因。

一个简化的路由矩阵如下：

| 场景 | 典型业务 | 推荐策略 | 分组选择 |
| --- | --- | --- | --- |
| `support_realtime` | AI 客服、付费用户在线问答 | 低超时、少重试、失败快速 fallback | 稳定官方分组优先 |
| `content_batch` | SEO/GEO 草稿、产品描述、营销邮件 | 异步队列、可重试、可降级 | 福利分组或官转分组 |
| `analytics_internal` | 数据分析、工单分类、运营摘要 | 中等超时、结构化日志、失败告警 | 官转分组优先 |
| `saas_feature` | 面向客户的应用内 AI 功能 | 按租户等级路由，保留审计字段 | 官转或稳定官方分组 |

## 三、curl：先验证分组、模型和超时策略

下面示例使用 OpenAI-compatible Chat Completions 形态。实际分组名称、模型名和 key 应以你的 ViralAPI 账号配置为准，不要把服务端 API key 暴露到前端。

```bash
export VIRALAPI_API_KEY="***"
export VIRALAPI_BASE_URL="https://viralapi.ai/v1"

curl --fail-with-body \
  --connect-timeout 5 \
  --max-time 40 \
  "${VIRALAPI_BASE_URL}/chat/completions" \
  -H "Authorization: Bearer ${VIRALAPI_API_KEY}" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: cost-router-demo-001" \
  -H "X-Business-Scenario: content_batch" \
  -d '{
    "model": "claude-sonnet-4",
    "messages": [
      {"role": "system", "content": "Return a concise structured answer."},
      {"role": "user", "content": "Draft a support knowledge-base answer for failed invoice payment."}
    ],
    "temperature": 0.3
  }'
```

上线前不要只看响应内容，还要记录：`request_id`、`scenario`、`model`、`group`、`attempt`、`latency_ms`、`status_code`、`fallback_from`、`estimated_tokens` 和 `tenant_id`。这些字段决定了后续能否做成本归因。

## 四、Python：一个可落地的成本路由器示例

下面示例演示如何把场景、分组、timeout、重试和 fallback 放在同一个服务端入口中。业务层不需要知道每个模型的细节。

```python
from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Sequence

from openai import OpenAI

logger = logging.getLogger("viralapi.cost_router")

@dataclass(frozen=True)
class Route:
    models: list[str]
    group: str
    timeout_seconds: float
    retries: int
    allow_fallback: bool

ROUTES = {
    "support_realtime": Route(
        models=["claude-sonnet-4", "gpt-4o-mini"],
        group="stable_official",
        timeout_seconds=18,
        retries=1,
        allow_fallback=True,
    ),
    "content_batch": Route(
        models=["claude-sonnet-4", "gemini-2.5-flash"],
        group="welfare_or_official_transfer",
        timeout_seconds=45,
        retries=3,
        allow_fallback=True,
    ),
    "analytics_internal": Route(
        models=["claude-sonnet-4", "gpt-4.1-mini"],
        group="official_transfer",
        timeout_seconds=30,
        retries=2,
        allow_fallback=True,
    ),
}

def build_client(timeout_seconds: float) -> OpenAI:
    return OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
        timeout=timeout_seconds,
        max_retries=0,
    )

def chat_for_scenario(
    messages: Sequence[dict[str, str]],
    scenario: str,
    request_id: str,
    tenant_id: str,
) -> str:
    route = ROUTES.get(scenario, ROUTES["analytics_internal"])
    client = build_client(route.timeout_seconds)
    last_error: Exception | None = None

    for model_index, model in enumerate(route.models):
        if model_index > 0 and not route.allow_fallback:
            break
        fallback_from = route.models[model_index - 1] if model_index > 0 else ""
        for attempt in range(1, route.retries + 1):
            started = time.monotonic()
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=list(messages),
                    temperature=0.2,
                    extra_headers={
                        "X-Request-ID": request_id,
                        "X-Business-Scenario": scenario,
                        "X-Tenant-ID": tenant_id,
                    },
                )
                latency_ms = round((time.monotonic() - started) * 1000)
                logger.info(
                    "llm_success request_id=%s tenant_id=%s scenario=%s group=%s model=%s attempt=%d latency_ms=%d fallback_from=%s",
                    request_id, tenant_id, scenario, route.group, model, attempt, latency_ms, fallback_from,
                )
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                latency_ms = round((time.monotonic() - started) * 1000)
                logger.warning(
                    "llm_error request_id=%s tenant_id=%s scenario=%s group=%s model=%s attempt=%d latency_ms=%d error=%s",
                    request_id, tenant_id, scenario, route.group, model, attempt, latency_ms, type(exc).__name__,
                )
                if attempt < route.retries:
                    time.sleep(min(8.0, 0.5 * (2 ** (attempt - 1))) + random.random() * 0.2)

    raise RuntimeError(f"LLM route failed request_id={request_id} scenario={scenario}") from last_error
```

这个例子里最重要的不是 `try/except`，而是把“哪个业务为什么花了多少钱”变成日志事实。后续可以按 `scenario`、`tenant_id`、`group` 和 `model` 做月度报表，决定哪些流量适合继续放在福利分组，哪些需要迁到更稳定的分组。

## 五、什么时候不应该降级或 fallback

不是所有失败都应该自动降级。下面几类请求要谨慎：

- 已经触发外部副作用的请求，例如自动发送邮件、自动提交工单；
- 强依赖特定模型输出格式的 JSON 解析链路；
- 高价值客户的实时客服会话，如果备用模型没有做过回归测试；
- 合规或安全策略依赖某个模型提示词行为的场景；
- 非幂等批处理任务，重复执行会造成重复扣费或重复写库。

如果必须 fallback，建议把输出再经过结构化校验，并在业务日志里标记 `fallback_from` 和 `fallback_reason`。

## 六、适合与不适合人群

**适合：**

- 有真实调用量、需要长期控制成本的小团队；
- 能自助接入 API、理解环境变量、日志和错误码的开发者；
- 有 AI 客服、内容生成、数据分析、内部工具或 SaaS 功能接入需求的团队；
- 需要 Claude、GPT、Gemini 多模型统一调用的业务；
- 同行渠道、代理或有稳定消耗的 API 用户。

**不适合：**

- 完全没有技术基础、希望全程代接入的小白；
- 只想白嫖、低预算试玩或没有真实业务调用的人；
- 高售后消耗但调用量很低的客户；
- 滥用、违规或高风险用途；
- 不能接受按场景选择稳定性与成本分组的用户。

## 七、FAQ

### 1. 成本控制是不是只选择最便宜的分组？

不是。成本控制的核心是按业务价值分层。批量草稿可以更成本敏感，AI 客服和付费 SaaS 功能则更需要稳定链路。

### 2. 福利分组适合生产吗？

福利分组约官方 1.5 折，更适合成本敏感、可重试、可异步处理的任务。核心实时业务建议优先评估官转分组或稳定官方分组。

### 3. 官转分组和稳定官方分组怎么选？

官转分组约官方 6 折，适合多数持续业务；稳定官方分组约官方 8 折，更适合客服、核心 SaaS 功能和对可用性更敏感的场景。

### 4. fallback 会不会影响输出质量？

会有可能。因此 fallback 模型必须提前做业务回归测试，尤其是结构化输出、客服话术和数据分析任务。

### 5. 如何开始接入？

先在服务端完成 OpenAI-compatible 基础调用，再加入 timeout、request_id、日志字段和场景路由。示例可参考 GitHub 仓库和 GitHub Pages 文档。

### 6. 联系方式是什么？

官网：https://viralapi.ai  
邮箱：miutayoung@gmail.com  
Telegram：viral_8866  
WeChat：viral_8866
