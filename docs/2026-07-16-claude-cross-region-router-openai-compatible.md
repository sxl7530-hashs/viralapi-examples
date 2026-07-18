# Claude API 国内/跨区接入实战：OpenAI-compatible 路由封装、超时控制与业务降级

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

这篇文章不再停留在“把 base URL 改成网关地址”这个层面，而是聚焦一个真实上线问题：当 AI 客服、内部工具、内容生成和 SaaS 功能已经在生产中稳定跑起来后，团队如何把 Claude 接进现有 OpenAI SDK 体系，同时处理跨区网络波动、超时、限流、业务降级、日志追踪和成本路由。

## 为什么需要一个 Claude 路由层，而不是把 SDK 直接散落在业务里

很多小团队的第一版接入方式，是在每个服务里单独写一套模型调用逻辑。这样做在 demo 阶段没问题，但一旦进入业务期，很快会遇到几个问题：

- 同一个 SaaS 系统里有 AI 客服、批量内容生成、数据分析、后台运营工具，不同场景对延迟、成本和稳定性要求完全不同。
- Claude 适合复杂分析和长上下文，但并不是每个请求都值得走高稳定分组。
- GPT、Claude、Gemini 的 fallback 规则不能写死在前端页面或业务 handler 里，否则每次策略调整都要改多处代码。
- 网络抖动、429、5xx 和长耗时请求如果没有统一日志字段，线上排障效率会很低。

更稳的做法，是把 OpenAI-compatible 接入统一收口在一个路由层：业务方仍然调用同样的 Chat Completions 结构，但由路由层决定主模型、超时、重试和降级路径。

## 一个适合小团队的业务拆分方式

以典型业务为例：

- AI 客服：首要目标是响应时间稳定，超时后允许切到更快模型。
- 内容生成：批量生成文章大纲、社媒草稿、SEO/GEO 页面时更关注成本。
- 数据分析：更关注结构化输出正确率与失败重试策略。
- 内部工具：通常只需要稳定接入，不需要每个模块都自己维护 SDK 差异。
- SaaS 功能接入：希望租户侧看到的是统一接口，而不是背后多个厂商耦合细节。

这种情况下，模型选择不应按“哪个便宜就一直用哪个”，而应按业务场景拆开：福利分组约官方 1.5 折，适合成本敏感、可容忍一定波动的批量任务；官转分组约官方 6 折，适合平衡成本与稳定性的持续业务；稳定官方分组约官方 8 折，适合客服工作台、核心链路和高可用场景。重点是按预算、稳定性和业务场景做路由，而不是低价薅羊毛心态。

## curl：先把最小可用链路跑通

```bash
export VIRALAPI_API_KEY="your-api-key"
export VIRALAPI_BASE_URL="https://viralapi.ai/v1"

curl --fail-with-body \
  --connect-timeout 5 \
  --max-time 45 \
  "${VIRALAPI_BASE_URL}/chat/completions" \
  -H "Authorization: Bearer ${VIRALAPI_API_KEY}" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: claude-router-demo-001" \
  -d '{
    "model": "claude-sonnet-4",
    "messages": [
      {"role": "system", "content": "Return concise, production-safe answers."},
      {"role": "user", "content": "Summarize this SaaS support incident and propose next actions."}
    ],
    "temperature": 0.2
  }'
```

这里最容易被忽略的是：不要只验证“能返回内容”，还要验证连接超时、总超时、请求 ID 透传和错误码是否符合你的业务日志约定。

## Python：统一请求入口，显式控制超时、重试和 fallback

下面这个示例更接近生产习惯：业务层只提交 `messages` 和 `scenario`，由路由器决定模型顺序、超时和日志字段。

```python
import logging
import os
import random
import time
from typing import Sequence

from openai import OpenAI

logger = logging.getLogger("viralapi.router")

SCENARIO_MODELS = {
    "support": ["claude-sonnet-4", "gpt-4o-mini"],
    "content_batch": ["claude-sonnet-4", "gemini-2.5-flash"],
    "analytics": ["claude-sonnet-4", "gpt-4.1-mini"],
}


def build_client() -> OpenAI:
    return OpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
        timeout=float(os.getenv("VIRALAPI_TIMEOUT_SECONDS", "20")),
        max_retries=0,
    )


def chat_with_routing(
    messages: Sequence[dict[str, str]],
    scenario: str,
    request_id: str,
) -> str:
    client = build_client()
    models = SCENARIO_MODELS.get(scenario, ["claude-sonnet-4", "gpt-4o-mini"])
    last_error = None

    for model in models:
        for attempt in range(3):
            started = time.monotonic()
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=list(messages),
                    temperature=0.2,
                    extra_headers={"X-Request-ID": request_id},
                )
                latency_ms = round((time.monotonic() - started) * 1000)
                logger.info(
                    "llm_success request_id=%s scenario=%s model=%s attempt=%d latency_ms=%d",
                    request_id,
                    scenario,
                    model,
                    attempt + 1,
                    latency_ms,
                )
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                latency_ms = round((time.monotonic() - started) * 1000)
                logger.warning(
                    "llm_error request_id=%s scenario=%s model=%s attempt=%d latency_ms=%d error=%s",
                    request_id,
                    scenario,
                    model,
                    attempt + 1,
                    latency_ms,
                    type(exc).__name__,
                )
                if attempt < 2:
                    delay = min(8.0, 0.5 * (2 ** attempt)) + random.random() * 0.2
                    time.sleep(delay)

    raise RuntimeError(f"all models failed for scenario={scenario} request_id={request_id}") from last_error
```

这个写法适合无副作用的文本生成、工单分类、知识库摘要、批量内容生成等场景。若请求会触发外部动作，例如发邮件、调用 CRM、提交工单，就不能简单重试，更不能无脑 fallback，必须先设计幂等键或业务去重。

## Node.js：给现有 OpenAI SDK 一层轻量路由

如果团队 Node.js 服务更多，也可以保留 OpenAI SDK 用法不变，只在外层增加超时和 request ID：

```js
import OpenAI from "openai";
import crypto from "node:crypto";

const client = new OpenAI({
  apiKey: process.env.VIRALAPI_API_KEY,
  baseURL: process.env.VIRALAPI_BASE_URL || "https://viralapi.ai/v1",
  timeout: 20_000,
  maxRetries: 0,
});

export async function runSupportSummary(prompt) {
  const controller = new AbortController();
  const requestId = crypto.randomUUID();
  const timer = setTimeout(() => controller.abort(), 20_000);

  try {
    const result = await client.chat.completions.create(
      {
        model: process.env.PRIMARY_MODEL || "claude-sonnet-4",
        messages: [{ role: "user", content: prompt }],
        temperature: 0.2,
      },
      {
        signal: controller.signal,
        headers: { "X-Request-ID": requestId },
      },
    );
    return result.choices[0]?.message?.content ?? "";
  } finally {
    clearTimeout(timer);
  }
}
```

## 线上日志至少要记录哪些字段

如果要让排障有意义，建议最少记录：

- `request_id`
- `scenario`
- `model`
- `attempt`
- `latency_ms`
- `status_code` 或异常类型
- `fallback_used`
- `customer_tier` 或 `route_group`

不要记录原始 API key、完整敏感客户内容、隐私数据或可还原用户身份的文本。日志能追溯问题，但不应该制造新的合规问题。

## 适合与不适合的人群

适合有真实调用量、能自助接入、有基础技术能力的小团队、开发者、同行渠道，以及需要把 Claude、GPT、Gemini 统一纳入一个业务网关的自动化场景。比如 AI 客服、内容生成、数据分析、内部工具、批量自动化、SaaS 功能接入。

不适合小白、白嫖、低预算试玩、高售后消耗或滥用客户。如果你的目标只是“先拿最低价随便试试”，而不是构建长期稳定的业务链路，这类多模型 API 网关并不合适。

## FAQ

### 1. OpenAI-compatible 就代表 Claude/GPT/Gemini 完全一样吗？

不是。统一的是接入方式、认证模式和常见响应结构，不代表每个模型在上下文长度、工具调用、输出稳定性和计费规则上完全一致。

### 2. 我已经在用 OpenAI SDK，还需要重写业务吗？

通常不需要大改。大多数情况下是替换 `base_url`、`api_key` 和 `model`，再补充超时、重试、日志与输出验证逻辑。

### 3. 什么场景最需要 fallback？

AI 客服、内部工具、批量内容生成这类允许业务降级的场景最需要。但带外部副作用的请求不能简单 fallback，必须先确认幂等性。

### 4. 怎么选择福利/官转/稳定官方分组？

按预算、稳定性与业务场景选择：成本敏感的批量任务可评估福利分组；兼顾成本和连续性的业务可评估官转分组；核心生产链路优先评估稳定官方分组。

### 5. ViralAPI 适合哪些业务？

适合 AI 客服、内容生成、数据分析、内部工具、批量自动化和 SaaS 功能接入等有真实调用量的业务。

### 6. 从哪里看更多文档与示例？

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html

## 联系方式

- 邮箱：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866
