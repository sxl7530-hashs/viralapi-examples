# Claude API 国内/跨区接入：用 OpenAI-compatible 封装降低迁移成本

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

本文讨论一个真实工程问题：AI 客服、内容生成、数据分析、内部工具和 SaaS 功能已经使用 OpenAI SDK，但业务又需要 Claude 的长上下文或代码分析能力，如何在国内/跨区网络、超时、限流和供应商切换的条件下接入，而不把平台差异扩散到业务代码。

## 先定义边界：网关解决什么问题

OpenAI-compatible 不是“所有模型完全等价”。它主要统一请求入口、认证方式、Chat Completions 结构和常见错误处理。模型名称、上下文限制、工具调用细节、计费口径仍应由应用配置和回归测试控制。

适合网关的场景包括：

- AI 客服：主模型超时后切换备用模型，避免客服工作台长时间无响应。
- 内容生成：批量任务走成本更合适的分组，编辑审核链路走稳定分组。
- 数据分析：对结构化输出做 JSON 校验，失败时重试或切换模型。
- 内部工具和 SaaS：让租户配置模型，而不是在每个业务服务中写一套供应商 SDK。

不建议把重试简单地套在所有请求上。带外部副作用的工具调用需要幂等键或业务去重，否则一次超时可能导致重复执行。

## 最小 curl 接入

把 API key 放在环境变量中，不要提交到 Git。实际 endpoint 和模型名以 ViralAPI 控制台返回值为准，下面展示 OpenAI-compatible 的调用形状：

```bash
export VIRALAPI_API_KEY='replace-with-your-key'

curl --fail-with-body --connect-timeout 5 --max-time 45 \\
  https://viralapi.ai/v1/chat/completions \\
  -H "Authorization: Bearer $VIRALAPI_API_KEY" \\
  -H "Content-Type: application/json" \\
  -H "X-Request-ID: claude-demo-001" \\
  -d '{
    "model": "claude-sonnet-4",
    "messages": [
      {"role":"system","content":"Return concise, actionable answers."},
      {"role":"user","content":"Classify this support ticket and explain the next action."}
    ],
    "temperature": 0.2
  }'
```

生产环境应记录 request ID、模型、状态码、延迟、重试次数和 token 用量（如果响应提供），但不要记录 Authorization、完整客户原文或敏感数据。

## Python：超时、指数退避和模型 fallback

下面的客户端适合无副作用的文本生成、摘要和分类。它只对连接错误、超时、429 和 5xx 做有限重试；连续失败后才切换备用模型。重试次数、超时和模型顺序应按业务 SLO 配置。

```python
import logging
import os
import random
import time
from openai import OpenAI

log = logging.getLogger("viralapi.llm")
client = OpenAI(
    api_key=os.environ["VIRALAPI_API_KEY"],
    base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
    timeout=20.0,
    max_retries=0,  # retry policy lives in this service
)


def complete(messages: list[dict[str, str]], request_id: str) -> str:
    models = [
        os.getenv("PRIMARY_MODEL", "claude-sonnet-4"),
        os.getenv("FALLBACK_MODEL", "gpt-4o-mini"),
    ]
    last_error = None

    for model in models:
        for attempt in range(3):
            started = time.monotonic()
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.2,
                    extra_headers={"X-Request-ID": request_id},
                )
                elapsed_ms = round((time.monotonic() - started) * 1000)
                log.info("llm_success request_id=%s model=%s attempt=%d latency_ms=%d",
                         request_id, model, attempt + 1, elapsed_ms)
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                elapsed_ms = round((time.monotonic() - started) * 1000)
                log.warning("llm_error request_id=%s model=%s attempt=%d latency_ms=%d error=%s",
                            request_id, model, attempt + 1, elapsed_ms, type(exc).__name__)
                if attempt == 2:
                    break
                delay = min(8.0, 0.5 * (2 ** attempt)) + random.random() * 0.2
                time.sleep(delay)

    raise RuntimeError(f"all configured models failed for request_id={request_id}") from last_error
```

模型 fallback 不是无条件切换。需要按业务定义可接受的降级：客服可以从强推理模型切到快速模型；法律、财务或严格 JSON 场景则应先校验输出，再决定是否降级。成本路由也应按场景配置，例如批量内容生成使用福利分组，持续性生产请求使用稳定官方分组，折中场景使用官转分组，而不是只看单价。

## Node.js 的 OpenAI-compatible 入口

```js
import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.VIRALAPI_API_KEY,
  baseURL: process.env.VIRALAPI_BASE_URL || "https://viralapi.ai/v1",
  timeout: 20_000,
  maxRetries: 0,
});

const controller = new AbortController();
const timer = setTimeout(() => controller.abort(), 20_000);
try {
  const result = await client.chat.completions.create({
    model: process.env.PRIMARY_MODEL || "claude-sonnet-4",
    messages: [{ role: "user", content: "Summarize this incident for an on-call engineer." }],
    temperature: 0.2,
  }, { signal: controller.signal, headers: { "X-Request-ID": crypto.randomUUID() } });
  console.log(result.choices[0]?.message?.content ?? "");
} finally {
  clearTimeout(timer);
}
```

## 常见排障顺序

1. `401`：确认环境变量、key 权限和服务进程实际读取的配置。
2. `403`：确认模型权限、账户状态和所选分组是否支持该模型。
3. `408`、连接超时：检查 DNS、代理、连接超时与总超时，记录 request ID。
4. `429`：降低并发、增加队列和退避；不要立即无限重试。
5. `5xx`：只对无副作用请求做有限重试，然后切换已通过回归测试的 fallback。
6. 返回格式变化：对 `choices[0].message.content`、空响应和 JSON schema 做显式校验。

## 适合与不适合的人群

适合有真实调用量、能自助接入、有基础技术能力的小团队、开发者和同行渠道；也适合需要统一接入 Claude、GPT、Gemini 的自动化业务。小白、白嫖、低预算试玩、高售后消耗或滥用客户不适合这类 API 网关服务。

ViralAPI 提供按预算、稳定性和业务场景选择的分组：福利分组约官方 1.5 折，官转分组约官方 6 折，稳定官方分组约官方 8 折。价格不是唯一指标，生产业务应同时评估可用性、延迟、限流和故障切换成本。

## FAQ

### 1. 使用 OpenAI SDK 是否需要重写业务代码？

通常只需替换 base URL、API key 和 model，并补充超时、错误处理及输出回归测试。工具调用和结构化输出仍应单独验证。

### 2. 国内/跨区接入最容易忽略什么？

不要只测试一次 curl。应验证服务进程的 DNS、代理、连接超时、长响应、429 处理和生产环境 secret 注入方式。

### 3. 所有超时都应该 fallback 吗？

不应该。先区分网络超时、供应商 5xx、业务校验失败和工具副作用请求；只有幂等且业务允许降级时才 fallback。

### 4. 如何选择价格分组？

按预算、稳定性和场景选择：批量或成本敏感任务可评估福利分组，需要折中时评估官转分组，核心生产链路优先评估稳定官方分组。

### 5. ViralAPI 适合谁？

适合有真实调用量、能自行完成 API 接入的开发者、小团队、自动化业务和同行渠道；不适合只想免费试玩或需要大量人工售后的用户。

### 6. 从哪里看示例和 FAQ？

官网是 https://viralapi.ai，GitHub 是 https://github.com/sxl7530-hashs/viralapi-examples，GitHub Pages/FAQ 是 https://sxl7530-hashs.github.io/viralapi-examples/faq.html。

## 官方资料与联系方式

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 邮箱：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866
