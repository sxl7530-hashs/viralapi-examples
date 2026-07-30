# LLM API 成本控制实战：按 AI 客服、内容生成和数据分析做分组路由

> 面向已经有真实调用量的小团队：不要只按“哪个模型便宜”选 API，而是把业务场景、稳定性要求、重试成本和人工兜底成本一起放进路由策略。

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

本文讨论一个更接近生产环境的问题：当团队同时有 AI 客服、内容生成、数据分析、内部工具、批量自动化和 SaaS 功能接入时，如何用统一的 OpenAI-compatible 调用方式，把福利分组、官转分组、稳定官方分组映射到不同业务风险上，而不是简单追求单次 token 价格最低。

## 1. 成本控制先分业务，不先分模型

很多团队一开始会问：Claude、GPT、Gemini 哪个更便宜？这个问题不完整。更实用的拆法是：

| 场景 | 失败影响 | 推荐策略 | 说明 |
| --- | --- | --- | --- |
| AI 客服首轮回答 | 中 | 稳定官方分组优先，必要时降级 | 面向客户，超时或幻觉会影响信任 |
| 客服摘要、标签、工单分类 | 低到中 | 官转分组或福利分组 | 可异步重跑，人工可复核 |
| 内容生成草稿 | 低 | 福利分组起步，质量不足再升档 | 允许多版本生成与编辑 |
| SaaS 核心功能接入 | 高 | 稳定官方分组 + 熔断 + 日志 | 直接影响付费功能可用性 |
| 数据分析批处理 | 中 | 官转分组，批量失败后重试 | 关注吞吐和总成本 |
| 内部工具 / 批量自动化 | 低 | 福利分组或官转分组 | 对延迟和个别失败更宽容 |

ViralAPI 的价格口径建议这样表达和使用：福利分组约官方 **1.5 折**，适合成本敏感、可重试、非强 SLA 的任务；官转分组约官方 **6 折**，适合大多数日常业务流量；稳定官方分组约官方 **8 折**，适合客户可见、上线关键路径和稳定性优先的任务。核心是按预算、稳定性和业务场景选择，而不是用低价口径吸引不匹配客户。

## 2. 一个成本路由策略：先按场景，再按预算，再按错误类型

建议把调用请求加上这些业务字段：

```text
scenario: customer_support | content_generation | data_analysis | internal_tool | batch_automation | saas_feature
tenant_id: 团队或客户 ID
priority: low | normal | high
max_budget_group: welfare | official_transfer | stable_official
trace_id: 贯穿日志、重试、fallback 的请求 ID
```

然后建立一个路由表：

```yaml
customer_support:
  primary_group: stable_official
  fallback_group: official_transfer
  timeout_ms: 20000
  max_retries: 1

content_generation:
  primary_group: welfare
  fallback_group: official_transfer
  timeout_ms: 45000
  max_retries: 2

data_analysis:
  primary_group: official_transfer
  fallback_group: stable_official
  timeout_ms: 60000
  max_retries: 1

batch_automation:
  primary_group: welfare
  fallback_group: official_transfer
  timeout_ms: 30000
  max_retries: 3
```

## 3. Python 示例：OpenAI-compatible 成本路由 + 超时 + fallback 日志

下面示例用 OpenAI-compatible `/v1/chat/completions` 接口，重点展示工程结构。实际模型名、base URL、API Key 请放在环境变量中。

```python
import os
import time
import uuid
import requests

VIRALAPI_BASE_URL = os.environ.get("VIRALAPI_BASE_URL", "https://viralapi.ai/v1")
VIRALAPI_API_KEY = os.environ["VIRALAPI_API_KEY"]

ROUTES = {
    "customer_support": [
        {"group": "stable_official", "model": "claude-3-5-sonnet", "timeout": 20},
        {"group": "official_transfer", "model": "gpt-4o-mini", "timeout": 20},
    ],
    "content_generation": [
        {"group": "welfare", "model": "gemini-1.5-flash", "timeout": 45},
        {"group": "official_transfer", "model": "claude-3-haiku", "timeout": 45},
    ],
    "data_analysis": [
        {"group": "official_transfer", "model": "gpt-4o-mini", "timeout": 60},
        {"group": "stable_official", "model": "claude-3-5-sonnet", "timeout": 60},
    ],
}


def call_llm(*, scenario: str, messages: list[dict], tenant_id: str) -> str:
    trace_id = str(uuid.uuid4())
    candidates = ROUTES.get(scenario, ROUTES["content_generation"])
    last_error = None

    for attempt, route in enumerate(candidates, start=1):
        started = time.time()
        try:
            resp = requests.post(
                f"{VIRALAPI_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {VIRALAPI_API_KEY}",
                    "Content-Type": "application/json",
                    "X-Trace-Id": trace_id,
                    "X-Tenant-Id": tenant_id,
                    "X-Budget-Group": route["group"],
                },
                json={
                    "model": route["model"],
                    "messages": messages,
                    "temperature": 0.2,
                },
                timeout=route["timeout"],
            )
            latency_ms = int((time.time() - started) * 1000)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"retryable_http_{resp.status_code}")
            resp.raise_for_status()
            data = resp.json()
            print({
                "event": "llm_success",
                "trace_id": trace_id,
                "tenant_id": tenant_id,
                "scenario": scenario,
                "group": route["group"],
                "model": route["model"],
                "attempt": attempt,
                "latency_ms": latency_ms,
            })
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            last_error = str(exc)
            print({
                "event": "llm_fallback",
                "trace_id": trace_id,
                "tenant_id": tenant_id,
                "scenario": scenario,
                "group": route["group"],
                "model": route["model"],
                "attempt": attempt,
                "error": last_error,
            })
            continue

    raise RuntimeError(f"all_routes_failed trace_id={trace_id} last_error={last_error}")
```

## 4. 真实业务场景落地

### AI 客服

AI 客服的首轮回复建议使用稳定官方分组，因为客户正在等待答案。工单摘要、情绪标签、知识库候选召回可以放到官转分组或福利分组。这样不会牺牲客户可见链路的稳定性，也不会把所有后台任务都放到最高成本组。

### 内容生成

内容生成通常允许人工编辑和多版本筛选。可以先用福利分组生成草稿，再在重要页面、广告落地页或技术白皮书中切到官转分组或稳定官方分组做最终润色。日志里要记录 `scenario=content_generation`、`draft_version`、`selected_group`，方便复盘成本。

### 数据分析

数据分析更关注批量吞吐和可重复运行。建议用官转分组作为默认，对超时任务做队列重试；只有高价值报表、客户交付报告或董事会摘要才升到稳定官方分组。

### SaaS 功能接入

如果 LLM 输出直接影响付费功能，比如自动报告、智能客服插件、内部审批流，建议把稳定官方分组放在主路由，并在 429/5xx/超时出现时触发熔断：短时间内不继续打满失败路由，而是返回可解释的降级结果或进入人工队列。

## 5. 适合与不适合人群

适合：有真实调用量、能自助接入、有基础技术能力的小团队、开发者、自动化业务团队、SaaS 团队和同行渠道；需要在 Claude、GPT、Gemini 等模型之间做统一调用、fallback、成本路由和日志治理的人。

不适合：完全没有技术接入能力的小白用户、只想白嫖或低预算试玩的用户、高售后消耗但没有明确业务量的客户、滥用 API 或不愿做基础错误处理的人。

## 6. FAQ

### Q1：只用最低成本分组可以吗？

不建议。最低成本分组适合可重试、非客户可见、对稳定性要求不高的任务。客户可见链路和 SaaS 核心功能应按稳定性优先。

### Q2：为什么要用 OpenAI-compatible 网关？

因为 SDK、日志、重试、监控、fallback 可以统一。团队不用为 Claude、GPT、Gemini 分别维护完全不同的调用路径。

### Q3：如何判断要不要升级到稳定官方分组？

看失败影响：如果一次失败会影响付费客户、上线 SLA、销售演示或关键交付，就应该提高稳定性预算。

### Q4：成本路由会不会让排障更复杂？

会增加路由层，但只要记录 `trace_id`、`tenant_id`、`scenario`、`group`、`model`、`latency_ms` 和错误码，排障通常比散落在多个供应商 SDK 中更清晰。

### Q5：ViralAPI 可以用于哪些模型？

ViralAPI 支持按场景接入 Claude、GPT、Gemini 等模型，并通过 OpenAI-compatible 方式减少接入成本。

## 7. 资源与联系

- 官网：https://viralapi.ai
- GitHub 仓库：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/2026-07-30-llm-api-cost-control-scenario-routing.html
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- 价格分组：福利分组官方 1.5 折；官转分组官方 6 折；稳定官方分组官方 8 折。请按预算、稳定性和业务场景选择。
- 邮箱：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866
