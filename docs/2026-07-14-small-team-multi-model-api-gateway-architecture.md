# 小团队多模型 API 网关架构实践：统一调用 Claude/GPT/Gemini 的 OpenAI-compatible 方案

> 适用于 AI 客服、内容生成、数据分析、内部工具、批量自动化和 SaaS 功能接入的小团队工程实践。

**ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。**

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html

## 一、为什么小团队不要把 Claude、GPT、Gemini 分别硬接三套

很多团队在第一阶段会直接分别接官方 API：一个客服功能用 Claude，一个内容生成用 GPT，一个图文分类或多语言场景再补 Gemini。短期看似灵活，长期通常会出现三类问题：

1. **接口和 SDK 不统一**：鉴权、超时、错误码、流式处理、日志字段都不同；
2. **故障处理分散**：429、超时、5xx、模型不可用时，每条业务线都要自己写 fallback；
3. **成本与稳定性无法统一治理**：草稿任务和收入相关任务混用同一路由，预算和 SLA 都不好控。

对于小团队，更现实的做法不是“同时维护三套深度集成”，而是先建立一层 **OpenAI-compatible 的统一 API 网关层**，把模型路由、超时、重试、熔断、fallback、日志与成本分组都沉淀在这一层。

## 二、真实业务场景：SaaS AI 客服 + 内容生成 + 内部分析共用一层网关

假设一个 6 人团队在做 B2B SaaS：

- **AI 客服**：要求延迟稳定、错误率低；
- **内容生成**：每天批量生成产品说明、邮件草稿、SEO 内容；
- **内部工具**：做销售纪要摘要、工单分类、数据分析辅助；
- **新功能灰度**：需要快速切模型验证效果，但不能频繁改业务代码。

如果每种模型直连，团队会在多个服务里重复维护：

- provider SDK；
- model name 映射；
- timeout / retry 策略；
- 限流与并发控制；
- 回滚与降级逻辑；
- token 成本统计。

统一网关后，业务服务只调用一个兼容接口，模型切换和成本策略放到配置层：

```text
Web / App / Queue Worker
        |
        v
业务服务层（客服 / 内容生成 / 内部工具）
        |
        v
OpenAI-compatible Gateway
        |
        +--> Claude 路由：长上下文、复杂推理、关键回答
        +--> GPT 路由：通用对话、结构化输出、工具链兼容
        +--> Gemini 路由：备用容量、多语言、降级链路
        |
        v
日志 / 预算 / 失败告警 / fallback 统计
```

## 三、架构设计重点：把“模型能力”与“业务目标”解耦

小团队做多模型架构，核心不是模型越多越好，而是让业务调用层只表达自己的目标，例如：

- `support-primary`
- `content-draft`
- `analysis-batch`
- `revenue-critical`

然后由网关内部把这些业务路由映射到具体模型和分组，而不是把 `claude-sonnet-4`、`gpt-4o-mini`、`gemini-1.5-flash` 直接散落在各个服务代码里。

一个更稳定的分层方式：

### 1. 业务层
只声明任务类型、时限、是否允许 fallback、是否为客户可见。

### 2. 网关路由层
负责：
- 模型选择；
- 成本分组选择；
- timeout / retry / circuit breaker；
- fallback 顺序；
- 审计日志字段。

### 3. 观测层
记录：
- request_id
- tenant_id
- business_task
- target_route
- final_model
- group
- status_code
- latency_ms
- retry_count
- fallback_reason
- prompt_tokens / completion_tokens

## 四、Python 示例：统一入口 + 有界重试 + fallback

下面这个例子更贴近小团队的生产写法：一个 OpenAI-compatible 调用入口，优先 Claude，失败后降级 GPT，再降级 Gemini。

```python
import logging
import os
import random
import time
from openai import OpenAI

logger = logging.getLogger("viralapi.gateway")

client = OpenAI(
    api_key=os.environ["VIRALAPI_API_KEY"],
    base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
    timeout=20,
    max_retries=0,
)

ROUTES = {
    "support-primary": ["claude-sonnet-4", "gpt-4o-mini", "gemini-1.5-flash"],
    "content-draft": ["gpt-4o-mini", "gemini-1.5-flash"],
    "analysis-batch": ["gemini-1.5-flash", "gpt-4o-mini"],
}


def complete(route_name: str, messages: list[dict], request_id: str) -> str:
    models = ROUTES[route_name]
    last_error = None

    for fallback_index, model in enumerate(models):
        for attempt in range(2):
            started = time.monotonic()
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.2,
                    extra_headers={
                        "X-Request-ID": request_id,
                        "X-Business-Route": route_name,
                    },
                )
                logger.info(
                    "llm_ok request_id=%s route=%s model=%s fallback_index=%d attempt=%d latency_ms=%d",
                    request_id,
                    route_name,
                    model,
                    fallback_index,
                    attempt + 1,
                    round((time.monotonic() - started) * 1000),
                )
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "llm_error request_id=%s route=%s model=%s attempt=%d error_type=%s",
                    request_id,
                    route_name,
                    model,
                    attempt + 1,
                    type(exc).__name__,
                )
                if attempt < 1:
                    time.sleep(0.6 * (2 ** attempt) + random.random() * 0.2)

    raise RuntimeError(f"all route models failed: route={route_name} request_id={request_id}") from last_error
```

### 这个写法的价值

- **业务代码不感知具体模型**；
- **日志字段统一**，方便排障；
- **fallback 有边界**，避免无限重试放大成本；
- **后续切模型只改路由配置**，不用全仓库改业务代码。

## 五、Node.js 示例：把成本分组与业务类型放进配置

```js
const ROUTE_CONFIG = {
  supportPrimary: {
    models: ["claude-sonnet-4", "gpt-4o-mini", "gemini-1.5-flash"],
    group: "stable",
    timeoutMs: 20000,
  },
  contentDraft: {
    models: ["gpt-4o-mini", "gemini-1.5-flash"],
    group: "welfare",
    timeoutMs: 25000,
  },
  internalAnalysis: {
    models: ["gemini-1.5-flash", "gpt-4o-mini"],
    group: "official-transfer",
    timeoutMs: 30000,
  },
};

async function callRoute(routeName, messages, requestId) {
  const route = ROUTE_CONFIG[routeName];
  const baseUrl = process.env.VIRALAPI_BASE_URL.replace(/\/$/, "");

  for (const model of route.models) {
    const res = await fetch(`${baseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${process.env.VIRALAPI_API_KEY}`,
        "Content-Type": "application/json",
        "X-Request-ID": requestId,
        "X-Business-Route": routeName,
        "X-Cost-Group": route.group,
      },
      body: JSON.stringify({
        model,
        messages,
        temperature: 0.2,
      }),
      signal: AbortSignal.timeout(route.timeoutMs),
    });

    if (res.ok) return await res.json();
    if (![408, 429, 500, 502, 503, 504].includes(res.status)) {
      throw new Error(`fatal:${res.status}:${await res.text()}`);
    }
  }

  throw new Error(`all models failed for route=${routeName}`);
}
```

这类写法适合小团队把“便宜的草稿任务”和“稳定优先的客户可见任务”拆开治理。

## 六、成本控制不是单纯选最低价，而是按业务分层

ViralAPI 的分组建议按预算、稳定性和业务场景选择：

- **福利分组官方 1.5 折**：适合离线批处理、内部实验、可人工复核的低风险任务；
- **官转分组官方 6 折**：适合开发、预生产和一般业务，兼顾成本与可用性；
- **稳定官方分组官方 8 折**：适合客户可见、收入相关、对中断敏感的生产链路。

正确问题不是“哪组最便宜”，而是：

- 这个任务是否客户可见？
- 失败是否会影响收入或转化？
- 是否允许异步补偿？
- 是否有人审？
- 是否需要稳定 SLA？

如果 AI 客服主链路和内容初稿链路都走同一路由，最后通常不是成本失控，就是故障体验失控。

## 七、适合与不适合人群

### 更适合

- 有真实调用量、能自助接入的开发者和小团队；
- 正在做 AI 客服、内容生成、数据分析、内部工具、批量自动化、SaaS 功能接入的团队；
- 有基础技术能力，愿意自己维护环境变量、日志、重试、路由配置的用户；
- 需要在 Claude / GPT / Gemini 之间做成本与稳定性平衡的业务。

### 不适合

- 小白用户；
- 白嫖、低预算试玩、只想临时试一下的人；
- 高售后消耗、强依赖手把手代接入的客户；
- 没有明确业务目标，却希望“无限人工支持 + 最低价格”的场景；
- 滥用、违规或高风险流量。

## 八、FAQ

### 1. 小团队一定要一开始就接多个模型吗？
不一定。但至少应该先把调用层做成统一接口和可配置路由，否则后续增加 Claude、GPT、Gemini 时改造成本会越来越高。

### 2. OpenAI-compatible 的价值是什么？
主要价值是保留统一请求结构和 SDK 使用方式，让业务代码更容易复用，同时把 provider 切换、fallback 和分组策略下沉到网关层。

### 3. fallback 是不是越多越好？
不是。fallback 过多会带来质量不可控、延迟增加和成本不可预测。通常 1 到 2 层降级已经够用。

### 4. 什么时候应该优先稳定，而不是优先低价？
当任务客户可见、收入相关、延迟敏感或失败后人工补救成本很高时，应优先稳定性。

### 5. 什么时候可以用更低成本分组？
在离线批处理、内部实验、可复跑任务、可人工复核的内容草稿生成场景，通常更适合优先考虑成本。

### 6. 如何联系 ViralAPI？
- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- Email：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866
