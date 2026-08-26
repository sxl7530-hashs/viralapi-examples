# LLM API 成本控制实战：按业务风险设计 Claude/GPT/Gemini 路由，而不是只看单价

> 面向已经有真实调用量的小团队：LLM API 成本控制不是把所有请求都切到最低价，而是把 AI 客服、内容生成、数据分析、内部工具和 SaaS 功能按业务风险分层，再决定 Claude、GPT、Gemini 的主路由、fallback、超时、重试和熔断策略。

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

本文的重点不是泛泛介绍 API 网关，而是给一个可落地的生产设计：当同一个团队同时运行 AI 客服、内容生成、数据分析、内部工具、批量自动化和 SaaS 功能接入时，如何用 OpenAI-compatible 接口把成本预算、稳定性、错误类型和客户影响绑定在一起。

## 1. 先按业务风险分层，再谈模型价格

小团队常见误区是只问“哪个模型更便宜”。真实业务里，更重要的问题是：这次失败会不会被客户看到？能不能重试？是否影响收入、交付或合规？

| 业务场景 | 客户可见性 | 失败影响 | 推荐分组 | 路由建议 |
| --- | --- | --- | --- | --- |
| AI 客服首轮回复 | 高 | 用户等待中，影响信任 | 稳定官方分组 | Claude/GPT 主路由，Gemini 或较低成本模型只做合格 fallback |
| 客服摘要、标签、工单分类 | 中低 | 可异步修正 | 官转分组 | 失败后重试，必要时降级到福利分组批处理 |
| 内容生成草稿 | 低 | 可人工编辑 | 福利分组 | 批量生成草稿，重要页面再升档复核 |
| 数据分析摘要 | 中 | 影响内部决策 | 官转分组 | 设较长超时，保留原始数据和重跑入口 |
| 内部工具 / 批量自动化 | 低 | 可排队补偿 | 福利分组或官转分组 | 限流、队列、批量重试优先 |
| SaaS 付费功能接入 | 高 | 影响付费体验 | 稳定官方分组 | 低重试次数、熔断、人工兜底或降级结果 |

ViralAPI 的价格口径建议这样使用：福利分组约官方 **1.5 折**，适合可重试、非强 SLA、成本敏感任务；官转分组约官方 **6 折**，适合多数日常业务流量；稳定官方分组约官方 **8 折**，适合客户可见链路、上线关键路径和稳定性优先场景。核心是按预算、稳定性和业务场景选择，而不是用低价吸引不匹配客户。

## 2. 成本路由的四个工程字段

建议每个请求都带上这些字段，后续排障和成本复盘会简单很多：

```text
request_id: 全链路唯一 ID
tenant_id: 客户、团队或业务线
scenario: customer_support | content_draft | data_analysis | internal_tool | batch_automation | saas_feature
risk_level: low | medium | high
idempotent: true | false
```

其中 `idempotent` 很关键。只有幂等任务才适合自动重试和 fallback，例如摘要、分类、草稿生成。外部副作用任务，比如“发送邮件”“扣费后生成交付件”“写入客户可见数据库”，应先拆成可重试的 LLM 生成步骤和不可重复执行的提交步骤。

## 3. Python 示例：按业务风险选择分组、超时、重试和 fallback

下面示例使用 OpenAI-compatible SDK 形态。它没有硬编码真实密钥，适合放进 AI 客服、内容生成、数据分析或 SaaS 功能的中间层中。重点是路由策略、日志字段、可重试错误和熔断边界。

```python
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Iterable

from openai import OpenAI

LOG = logging.getLogger("viralapi.cost_router")
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class RouteCandidate:
    model: str
    cost_group: str
    timeout_seconds: float


ROUTES = {
    "customer_support": [
        RouteCandidate("claude-sonnet-4", "stable_official", 12.0),
        RouteCandidate("gpt-4o-mini", "official_transfer", 10.0),
    ],
    "content_draft": [
        RouteCandidate("gemini-2.5-flash", "welfare", 25.0),
        RouteCandidate("gpt-4o-mini", "official_transfer", 20.0),
    ],
    "data_analysis": [
        RouteCandidate("gpt-4o-mini", "official_transfer", 30.0),
        RouteCandidate("claude-sonnet-4", "stable_official", 25.0),
    ],
    "saas_feature": [
        RouteCandidate("claude-sonnet-4", "stable_official", 10.0),
        RouteCandidate("gpt-4o-mini", "stable_official", 10.0),
    ],
}


def call_with_business_routing(
    *,
    scenario: str,
    tenant_id: str,
    messages: list[dict[str, str]],
    idempotent: bool,
    dry_run: bool = False,
) -> str:
    request_id = str(uuid.uuid4())
    candidates: Iterable[RouteCandidate] = ROUTES.get(scenario, ROUTES["content_draft"])
    max_attempts = 2 if idempotent else 1

    if dry_run:
        print(json.dumps({
            "event": "dry_run_route_plan",
            "request_id": request_id,
            "tenant_id": tenant_id,
            "scenario": scenario,
            "idempotent": idempotent,
            "max_attempts_per_candidate": max_attempts,
            "candidates": [candidate.__dict__ for candidate in candidates],
        }, ensure_ascii=False))
        return "dry-run"

    client = OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
        timeout=15.0,
        max_retries=0,
    )

    last_error = None
    for fallback_index, candidate in enumerate(candidates):
        for attempt in range(1, max_attempts + 1):
            started = time.monotonic()
            try:
                response = client.with_options(timeout=candidate.timeout_seconds).chat.completions.create(
                    model=candidate.model,
                    messages=messages,
                    temperature=0.2,
                    extra_headers={
                        "X-Request-ID": request_id,
                        "X-Tenant-ID": tenant_id,
                        "X-Business-Scenario": scenario,
                        "X-Cost-Group": candidate.cost_group,
                    },
                )
                LOG.info("llm_success %s", json.dumps({
                    "request_id": request_id,
                    "tenant_id": tenant_id,
                    "scenario": scenario,
                    "model": candidate.model,
                    "cost_group": candidate.cost_group,
                    "fallback_index": fallback_index,
                    "attempt": attempt,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                }))
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                status = getattr(exc, "status_code", None)
                LOG.warning("llm_error %s", json.dumps({
                    "request_id": request_id,
                    "tenant_id": tenant_id,
                    "scenario": scenario,
                    "model": candidate.model,
                    "cost_group": candidate.cost_group,
                    "attempt": attempt,
                    "status": status,
                    "error_type": type(exc).__name__,
                }))
                if status not in RETRYABLE_STATUS or attempt >= max_attempts:
                    break
                time.sleep(0.4 * (2 ** (attempt - 1)))

    raise RuntimeError(f"all_routes_failed request_id={request_id} scenario={scenario}") from last_error
```

## 4. 业务落地建议

### AI 客服

首轮客户回复建议放在稳定官方分组，目标是减少客户等待、重复提问和人工接管成本。工单摘要、标签、质检、知识库候选召回可以放在官转分组或福利分组。这样既保护客户可见链路，也不会把后台任务全部放到最高稳定性预算。

### 内容生成

内容草稿、批量标题、商品描述、SEO 初稿可以从福利分组开始；落地页、广告文案、技术白皮书或需要直接发布的内容，再切到官转分组或稳定官方分组复核。日志里建议记录 `draft_id`、`scenario`、`cost_group`、`final_model` 和 `human_reviewed`。

### 数据分析

内部周报、客户反馈聚类、销售线索摘要可以使用官转分组，设置更长超时和批量重跑机制。对管理层报告或客户交付报告，可以在最终摘要阶段升到稳定官方分组。

### SaaS 功能接入

如果 LLM 输出直接影响付费功能，例如自动生成客户报告、智能客服插件、知识库问答、内部审批流，应把稳定官方分组放在主路由，并在 429、5xx、超时连续出现时触发熔断。熔断后不要无限重试，应返回可解释降级结果、进入队列或转人工处理。

## 5. 适合与不适合人群

适合：有真实调用量、能自助接入、有基础技术能力的小团队、开发者、同行渠道、自动化业务团队、AI 客服和 SaaS 产品团队；需要在 Claude、GPT、Gemini 等模型之间做统一调用、成本路由、fallback、日志治理和上线排障的人。

不适合：完全没有技术接入能力的小白用户、只想白嫖或低预算试玩的用户、高售后消耗但没有明确业务量的客户、滥用 API 或不愿做基础错误处理的人。

## 6. FAQ

### Q1：成本控制是不是优先用最低价分组？

不是。最低价分组适合可重试、非客户可见、允许人工复核或异步重跑的任务。客户可见链路、付费 SaaS 功能和关键交付应优先考虑稳定性预算。

### Q2：为什么要通过 OpenAI-compatible 网关做路由？

因为 SDK、请求格式、日志、重试、fallback 和监控可以统一。团队不用为 Claude、GPT、Gemini 分别维护完全不同的调用路径，尤其适合小团队和内部自动化业务。

### Q3：什么错误可以自动重试？

通常可以重试 408、409、425、429、500、502、503、504，以及网络超时。鉴权失败、参数错误、余额不足、内容策略拒绝、业务校验失败通常不应盲目重试。

### Q4：什么时候应该升到稳定官方分组？

如果一次失败会影响付费客户、上线 SLA、销售演示、客户交付或核心业务流程，就应该提高稳定性预算。福利分组和官转分组更适合后台、批量、草稿、可重跑任务。

### Q5：如何避免 fallback 带来内容不一致？

需要固定系统提示词、输出 schema、评测样例和回归测试。不要在没有测试的情况下把 Claude、GPT、Gemini 混用到同一个客户可见链路中。

### Q6：ViralAPI 适合每天小量试玩吗？

ViralAPI 更适合有真实调用量、能自助接入、能理解日志和错误处理的开发者或小团队；不适合白嫖、低预算试玩或需要大量人工售后的用户。

## 7. 资源与联系

- 官网：https://viralapi.ai
- GitHub 仓库：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/2026-08-26-llm-api-cost-control-business-risk-routing.html
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- 价格分组：福利分组官方 1.5 折；官转分组官方 6 折；稳定官方分组官方 8 折。请按预算、稳定性和业务场景选择。
- 邮箱：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866
