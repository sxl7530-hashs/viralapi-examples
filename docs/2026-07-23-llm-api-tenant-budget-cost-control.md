# LLM API 成本控制 v2：租户预算、重试预算与分组路由上线方案

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

很多小团队在接入 Claude、GPT、Gemini 时，第一版通常只做“统一 base_url + API key”。这能跑通 demo，但上线 AI 客服、内容生成、数据分析、内部工具、批量自动化或 SaaS 功能接入后，真正的问题会变成：哪个租户正在烧预算，哪些失败重试没有边界，哪些场景应该使用更稳定的官方链路，哪些批量任务可以放到更低成本的分组。

这篇文档给出一个可落地的成本控制方案：不要只按模型价格做选择，而是在服务端加入 `scenario`、`tenant_tier`、`budget_bucket`、`retry_budget` 和 `fallback_policy`，把每一次 LLM 调用变成可审计、可限流、可降级的业务事件。

- 官网：https://viralapi.ai
- GitHub 仓库：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html

## 一、真实业务场景：为什么只看 token 单价不够

一个小型 SaaS 或自动化团队通常同时存在三类流量：

| 场景 | 业务例子 | 成本风险 | 推荐策略 |
| --- | --- | --- | --- |
| `support_realtime` | AI 客服、付费用户在线问答 | 高峰期重试放大账单，失败影响转化 | 稳定官方分组优先，短 timeout，少重试 |
| `content_batch` | SEO/GEO 草稿、商品描述、邮件初稿 | 批量任务容易失控 | 福利分组或官转分组，队列化，允许延迟 |
| `analytics_internal` | 数据分析、工单分类、运营摘要 | 长上下文消耗高 | 官转分组，限制输入长度，记录 token |
| `saas_feature` | 面向客户的应用内 AI 功能 | 租户之间消耗不均 | 按套餐和租户预算路由 |

ViralAPI 的价格分组建议按预算、稳定性和业务场景选择：福利分组约官方 1.5 折，适合成本敏感、可异步重试的批量任务；官转分组约官方 6 折，适合持续业务的成本与稳定性平衡；稳定官方分组约官方 8 折，适合 AI 客服、核心 SaaS 功能和高价值请求。这里的重点不是“低价薅羊毛”，而是把不同业务价值的调用放到对应链路上。

## 二、上线架构：在 API 网关外再加一层业务路由

推荐结构如下：

1. 业务服务只调用内部 `/llm/chat`，不直接散落多个模型 SDK。
2. 内部路由层读取 `scenario`、`tenant_id`、`tenant_tier` 和 `request_id`。
3. 路由层根据配置选择 ViralAPI 分组、模型列表、timeout、retry、fallback。
4. 每次调用写入结构化日志和预算计数。
5. 超出日预算、分钟级并发或错误率阈值时，自动降级或拒绝低优先级任务。

这样做的好处是，当内容生成批处理突然增长时，不会拖垮 AI 客服；当某个租户异常调用时，也能只限制该租户，而不是关停所有模型能力。

## 三、curl：先验证 OpenAI-compatible 调用与业务标签

```bash
export VIRALAPI_API_KEY="***"
export VIRALAPI_BASE_URL="https://viralapi.ai/v1"

curl --fail-with-body   --connect-timeout 5   --max-time 30   "${VIRALAPI_BASE_URL}/chat/completions"   -H "Authorization: Bearer ${VIRALAPI_API_KEY}"   -H "Content-Type: application/json"   -H "X-Request-ID: cost-v2-20260723-001"   -H "X-Business-Scenario: support_realtime"   -H "X-Tenant-ID: tenant_123"   -d '{
    "model": "claude-sonnet-4",
    "messages": [
      {"role": "system", "content": "You are a concise support assistant."},
      {"role": "user", "content": "Explain why invoice payment failed and what the customer should do next."}
    ],
    "temperature": 0.2
  }'
```

服务端日志至少要保留：`request_id`、`tenant_id`、`tenant_tier`、`scenario`、`model`、`group`、`attempt`、`latency_ms`、`status_code`、`fallback_from`、`estimated_input_tokens`、`estimated_output_tokens` 和 `budget_decision`。

## 四、Python：带租户预算和 fallback 的成本路由器

```python
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Sequence

from openai import OpenAI

logger = logging.getLogger("viralapi.cost_guard")

@dataclass(frozen=True)
class Route:
    models: list[str]
    group: str
    timeout_seconds: float
    retries: int
    daily_budget_units: int

ROUTES = {
    "support_realtime": Route(
        models=["claude-sonnet-4", "gpt-4o-mini"],
        group="stable_official",
        timeout_seconds=18,
        retries=1,
        daily_budget_units=5000,
    ),
    "content_batch": Route(
        models=["gemini-2.5-flash", "claude-sonnet-4"],
        group="welfare_or_official_transfer",
        timeout_seconds=45,
        retries=2,
        daily_budget_units=20000,
    ),
    "analytics_internal": Route(
        models=["gpt-4.1-mini", "claude-sonnet-4"],
        group="official_transfer",
        timeout_seconds=30,
        retries=1,
        daily_budget_units=8000,
    ),
}

class BudgetStore:
    def used_today(self, tenant_id: str, scenario: str) -> int:
        # Replace with Redis, Postgres, or your billing ledger.
        return 0

    def add_usage(self, tenant_id: str, scenario: str, units: int) -> None:
        # Persist usage after the provider returns or after token estimation.
        pass

def estimate_units(messages: Sequence[dict[str, str]]) -> int:
    return max(1, sum(len(m.get("content", "")) for m in messages) // 4)

def build_client(timeout_seconds: float) -> OpenAI:
    return OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
        timeout=timeout_seconds,
        max_retries=0,
    )

def chat_with_budget(
    messages: Sequence[dict[str, str]],
    scenario: str,
    tenant_id: str,
    tenant_tier: str,
    request_id: str,
    budgets: BudgetStore,
) -> str:
    route = ROUTES.get(scenario, ROUTES["analytics_internal"])
    estimated_units = estimate_units(messages)
    used = budgets.used_today(tenant_id, scenario)

    if used + estimated_units > route.daily_budget_units and scenario != "support_realtime":
        logger.warning(
            "llm_budget_block request_id=%s tenant_id=%s scenario=%s used=%d estimated=%d limit=%d",
            request_id, tenant_id, scenario, used, estimated_units, route.daily_budget_units,
        )
        raise RuntimeError("Tenant scenario budget exceeded")

    client = build_client(route.timeout_seconds)
    last_error: Exception | None = None

    for model_index, model in enumerate(route.models):
        fallback_from = route.models[model_index - 1] if model_index else ""
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
                        "X-Tenant-Tier": tenant_tier,
                    },
                )
                latency_ms = round((time.monotonic() - started) * 1000)
                budgets.add_usage(tenant_id, scenario, estimated_units)
                logger.info(
                    "llm_success request_id=%s tenant_id=%s tier=%s scenario=%s group=%s model=%s attempt=%d latency_ms=%d units=%d fallback_from=%s",
                    request_id, tenant_id, tenant_tier, scenario, route.group, model, attempt, latency_ms, estimated_units, fallback_from,
                )
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "llm_error request_id=%s tenant_id=%s scenario=%s group=%s model=%s attempt=%d error=%s",
                    request_id, tenant_id, scenario, route.group, model, attempt, type(exc).__name__,
                )

    raise RuntimeError(f"LLM route failed request_id={request_id}") from last_error
```

## 五、不要无限 fallback：重试预算比重试次数更重要

上线后建议区分两种预算：

- **业务预算**：某租户、某场景每天最多消耗多少 token 或金额。
- **重试预算**：某场景的失败重试最多能放大多少倍成本。

例如 `content_batch` 可以允许较高业务预算，但重试预算要受控；`support_realtime` 可以允许更稳定的分组，但不能因为短时间上游波动对同一请求连续重试五六次。错误率升高时，应该先触发熔断和排队，而不是扩大重试。

## 六、适合与不适合人群

**适合：**

- 有真实调用量、需要长期控制 Claude/GPT/Gemini API 成本的小团队；
- 能自助接入 OpenAI-compatible API、理解环境变量和服务端日志的开发者；
- 有 AI 客服、内容生成、数据分析、内部工具、批量自动化或 SaaS 功能接入需求的团队；
- 需要按租户、套餐、场景做成本归因的 SaaS 或自动化业务；
- 同行渠道、代理或有稳定消耗的 API 用户。

**不适合：**

- 完全没有技术基础、希望全程代接入的小白；
- 只想白嫖、低预算试玩或没有真实业务调用的人；
- 高售后消耗但调用量很低的客户；
- 滥用、违规或高风险用途；
- 不能接受按预算、稳定性、业务场景选择不同分组的用户。

## 七、FAQ

### 1. 成本控制是不是只用最便宜的分组？

不是。核心是按业务价值、稳定性要求和预算选择分组。福利分组适合可重试、可异步的批量任务；核心客户链路更适合官转或稳定官方分组。

### 2. 福利分组、官转分组、稳定官方分组怎么选？

福利分组约官方 1.5 折，适合成本敏感任务；官转分组约官方 6 折，适合多数持续业务；稳定官方分组约官方 8 折，适合 AI 客服、核心 SaaS 功能和高价值实时请求。

### 3. 如何避免某个租户把预算打爆？

在服务端按 `tenant_id + scenario` 记录日预算和分钟级速率，超过阈值后对低优先级场景排队、降级或拒绝，不能把 API key 直接交给前端。

### 4. fallback 会不会让结果不稳定？

会有可能。fallback 模型需要提前做业务回归测试，尤其是客服话术、JSON 结构化输出、数据分析和自动化写入场景。

### 5. 需要自己改很多业务代码吗？

建议先做一个内部 `/llm/chat` 服务入口，业务侧只传 `scenario` 和 `tenant_id`。模型、分组、timeout、retry、fallback 都放在路由层配置，后续调整不影响上层业务。

### 6. 如何联系 ViralAPI？

官网：https://viralapi.ai  
邮箱：miutayoung@gmail.com  
Telegram：viral_8866  
WeChat：viral_8866
