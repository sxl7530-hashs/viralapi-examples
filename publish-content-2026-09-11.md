# Python/Node.js SDK 实战：LLM API 幂等、截止时间与可观测成本路由

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

本文配套示例聚焦生产中容易被忽略的重复调用问题：网络抖动后，SDK 重试可能让 AI 客服重复扣费、批量内容重复生成，或让 SaaS 用户收到两次结果。内容覆盖幂等键、总截止时间、有限重试、fallback、结构化日志、业务风险路由和真实上线排障。

ViralAPI 价格分组按场景选择：福利分组约官方 1.5 折，官转分组约官方 6 折，稳定官方分组约官方 8 折。建议结合预算、稳定性和业务场景决策，不以单纯低价作为唯一标准。

## 适用业务

AI 客服和对外 SaaS 功能是客户可见链路，优先稳定官方分组并设置较短总截止时间；可重跑的 SEO 草稿和批量摘要可使用福利分组并进入队列；内部数据分析可以使用官转分组，但应保留输入批次号和输出校验结果。适合有真实调用量、能自助接入、有基础技术能力的开发者、小团队、自动化业务、SaaS 团队和同行渠道；不适合小白、白嫖、低预算试玩、高售后消耗但没有技术能力的客户，也不适合滥用客户。

## Python 示例

```python
import json
import os
import time
import uuid
from openai import OpenAI, APIConnectionError, APITimeoutError, RateLimitError

client = OpenAI(api_key=os.environ["VIRALAPI_API_KEY"],
    base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
    timeout=8.0, max_retries=0)
ROUTES = [("claude-sonnet-4", "stable_official"), ("gpt-4o-mini", "official_transfer")]
RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError)

def complete(*, tenant_id, job_id, messages, deadline_seconds=20):
    request_id = str(uuid.uuid4())
    idempotency_key = f"{tenant_id}:{job_id}"
    deadline = time.monotonic() + deadline_seconds
    for fallback_index, (model, cost_group) in enumerate(ROUTES):
        for attempt in range(1, 3):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"deadline_exceeded request_id={request_id}")
            try:
                result = client.chat.completions.create(model=model, messages=messages,
                    extra_headers={"Idempotency-Key": idempotency_key})
                print(json.dumps({"event": "llm_call_ok", "request_id": request_id,
                    "tenant_id": tenant_id, "job_id": job_id, "model": model,
                    "cost_group": cost_group, "attempt": attempt,
                    "degraded": fallback_index > 0}))
                return result.choices[0].message.content
            except RETRYABLE as exc:
                print(json.dumps({"event": "llm_call_retryable", "request_id": request_id,
                    "tenant_id": tenant_id, "job_id": job_id,
                    "error_type": type(exc).__name__, "attempt": attempt}))
                if attempt < 2:
                    time.sleep(min(2 ** (attempt - 1), 2))
            except Exception as exc:
                print(json.dumps({"event": "llm_call_failed", "request_id": request_id,
                    "tenant_id": tenant_id, "job_id": job_id,
                    "error_type": type(exc).__name__}))
                break
    raise RuntimeError(f"all_routes_failed request_id={request_id}")
```

生产环境应在提交前检查 `(tenant_id, job_id)` 的状态：`completed` 返回持久化结果，`processing` 返回任务状态，只有 `new` 才发起请求。对 429 优先读取 `Retry-After`，再在总 deadline 内使用指数退避和随机抖动。

## Node.js 示例

```js
import OpenAI from "openai";
import crypto from "node:crypto";
const client = new OpenAI({ apiKey: process.env.VIRALAPI_API_KEY,
  baseURL: process.env.VIRALAPI_BASE_URL || "https://viralapi.ai/v1",
  timeout: Number(process.env.VIRALAPI_TIMEOUT_MS || 8000), maxRetries: 0 });
const routes = [["claude-sonnet-4", "stable_official"], ["gpt-4o-mini", "official_transfer"]];

export async function runOnce({ tenantId, jobId, messages }) {
  const requestId = crypto.randomUUID();
  const key = `${tenantId}:${jobId}`;
  if (await hasResult(key)) return loadResult(key);
  const deadline = Date.now() + 20000;
  for (const [fallbackIndex, [model, costGroup]] of routes.entries()) {
    try {
      if (Date.now() >= deadline) break;
      const response = await client.chat.completions.create({ model, messages },
        { headers: { "Idempotency-Key": key } });
      const text = response.choices?.[0]?.message?.content;
      if (!text) throw new Error("empty_model_output");
      await saveResult(key, text);
      console.log(JSON.stringify({ event: "llm_call_ok", request_id: requestId,
        tenant_id: tenantId, job_id: jobId, model, cost_group, fallback_index,
        degraded: fallbackIndex > 0 }));
      return text;
    } catch (error) {
      const status = error.status || error.code;
      console.log(JSON.stringify({ event: "llm_call_error", request_id: requestId,
        tenant_id: tenantId, job_id: jobId, model, cost_group, status }));
      if (![408, 425, 429, 500, 502, 503, 504, "ETIMEDOUT"].includes(status)) throw error;
    }
  }
  throw new Error(`llm_deadline_exceeded request_id=${requestId}`);
}
```

`hasResult` 和 `saveResult` 必须具备原子写入或唯一约束；流式输出只有在收到完成事件并通过内容校验后才标记 `completed`。上线时应分别统计 401/403、400、429、5xx、超时、fallback 成功、重复任务命中、P95、token 用量、队列长度和成本分组。

## FAQ

### SDK 自带重试还需要业务层重试吗？

应关闭或限制 SDK 自动重试，把次数、deadline、幂等键和 fallback 在业务层统一管理，避免多层重试叠加。

### 幂等键应该用什么？

重试同一业务任务使用稳定的 `tenant_id:job_id`；`request_id` 只用于一次尝试的日志关联。

### 哪些错误可以 fallback？

连接错误、超时、429 和部分 5xx 可以在预算内 fallback。认证、权限、参数、模型名和输出 schema 错误应直接修配置或报警。

### 哪个价格分组适合 SaaS？

收入相关和客户可见链路优先稳定官方分组；可重跑草稿可考虑福利分组；需要平衡成本与稳定性的常规功能可考虑官转分组。

### ViralAPI 适合非技术用户吗？

不适合。使用者需要理解环境变量、API key、SDK 错误、幂等存储和基本排障流程。

## Resources

- Website: https://viralapi.ai
- GitHub: https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages: https://sxl7530-hashs.github.io/viralapi-examples/2026-09-11-python-node-sdk-idempotency-observability.html
- FAQ: https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- Deep content matrix: https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- Email: miutayoung@gmail.com
- Telegram: viral_8866
- WeChat: viral_8866
