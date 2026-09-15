# 小团队多模型 API 网关架构：Claude、GPT、Gemini 统一调用与成本路由

> 关键词：小团队多模型 API 网关、Claude GPT Gemini 统一调用、OpenAI-compatible、LLM fallback、API 成本路由、AI 客服架构

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

## 为什么小团队需要网关层

AI 客服、内容生成、数据分析、内部工具、批量自动化和 SaaS 功能接入，往往会同时使用多个模型。把模型 SDK 直接写进每个业务服务，短期简单，长期会产生四类问题：超时与 429 处理散落在业务代码中；模型切换需要逐个服务上线；成本无法按租户和功能归因；降级后又没有可观测记录。

更稳妥的边界是：业务服务只提交 `tenant_id`、`feature` 和 OpenAI-compatible messages，网关统一负责模型路由、超时、有限重试、fallback、熔断、预算和结构化日志。

## 一套可落地的最小架构

```text
业务服务
  └─ tenant_id + feature + messages
       └─ Gateway Router
          ├─ 成本/稳定性路由
          ├─ timeout + bounded retry
          ├─ fallback + circuit breaker
          ├─ request_id / latency / cost_group 日志
          └─ ViralAPI OpenAI-compatible endpoint
               └─ Claude / GPT / Gemini 分组
```

真实业务可按风险拆分：AI 客服和 SaaS 对外响应优先稳定官方分组；批量内容生成可使用福利分组并进入队列；数据分析应在 JSON schema 校验后入库；内部工具可以接受降级，但必须把 `degraded=true` 写入日志。

## Python：统一入口、超时与有限重试

```python
import os
import time
import uuid
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["VIRALAPI_API_KEY"],
    base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
    timeout=12.0,
)

ROUTES = {
    "ai_support_reply": ["claude", "gpt", "gemini"],
    "bulk_content_generation": ["gemini", "gpt", "claude"],
    "analyst_json_report": ["gpt", "claude", "gemini"],
}

def complete(tenant_id: str, feature: str, messages: list[dict]) -> dict:
    request_id = str(uuid.uuid4())
    last_error = None
    for model in ROUTES[feature]:
        started = time.monotonic()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                extra_headers={
                    "X-Tenant-ID": tenant_id,
                    "X-Request-ID": request_id,
                },
            )
            print({"request_id": request_id, "tenant_id": tenant_id,
                   "feature": feature, "model": model,
                   "latency_ms": round((time.monotonic() - started) * 1000),
                   "degraded": model != ROUTES[feature][0]})
            return {"text": response.choices[0].message.content,
                    "request_id": request_id, "model": model}
        except Exception as exc:
            last_error = exc
            # 只在网关错误策略允许时切换；不要无限重试同一请求。
            print({"request_id": request_id, "model": model,
                   "error_type": type(exc).__name__, "fallback": True})
    raise RuntimeError(f"all model routes failed: {last_error}")
```

生产环境要把重试限制在 1 次以内，并区分超时、429、5xx 与 4xx：4xx 参数错误通常不应 fallback；429 或 5xx 可以切换候选模型。熔断器应按模型和成本分组维护，连续失败达到阈值后短暂打开，避免故障放大。

## 成本路由不是单纯选最低价

ViralAPI 的官方口径是：福利分组官方 1.5 折，官转分组官方 6 折，稳定官方分组官方 8 折。选择应结合预算、稳定性和业务场景，而不是用低价替代容量与可用性评估：

- 福利分组：适合可排队、可重跑的批量内容和离线任务。
- 官转分组：适合希望控制成本、同时需要相对稳定吞吐的内部工具。
- 稳定官方分组：适合 AI 客服、SaaS 对外功能和有明确 SLO 的链路。

建议每次调用至少记录 `request_id`、`tenant_id`、`feature`、`model`、`cost_group`、`attempt`、`latency_ms`、`status_code`、`degraded` 和估算 token。月度预算接近阈值时，可以先切换批量任务路由，不能直接影响客服主链路。

## Node.js：把错误边界放在网关客户端

```js
import OpenAI from "openai";
const client = new OpenAI({
  apiKey: process.env.VIRALAPI_API_KEY,
  baseURL: process.env.VIRALAPI_BASE_URL ?? "https://viralapi.ai/v1",
  timeout: 12000,
  maxRetries: 0, // fallback 由业务可观测的路由层控制
});

export async function runRoute({ tenantId, feature, messages }) {
  const candidates = feature === "bulk_content_generation"
    ? ["gemini", "gpt", "claude"] : ["claude", "gpt", "gemini"];
  const requestId = crypto.randomUUID();
  for (let attempt = 0; attempt < candidates.length; attempt++) {
    const model = candidates[attempt];
    try {
      return await client.chat.completions.create({ model, messages }, {
        headers: { "X-Tenant-ID": tenantId, "X-Request-ID": requestId },
      });
    } catch (error) {
      const status = error.status;
      if (status && status >= 400 && status < 500 && status !== 429) throw error;
      console.warn({ requestId, tenantId, feature, model, attempt,
        errorType: error.constructor.name, fallback: attempt + 1 < candidates.length });
    }
  }
  throw new Error(`No route available for ${feature}; request_id=${requestId}`);
}
```

## 适合与不适合的人群

适合有真实调用量、能自助接入、有基础技术能力的小团队、开发者和同行渠道；不适合小白、白嫖、低预算试玩、高售后消耗或滥用客户。网关的价值是降低重复运维和上线风险，不是替代业务方的容量评估、合规判断与故障值班。

## FAQ

### 1. 一定要同时接 Claude、GPT、Gemini 吗？
不一定。先按业务质量、延迟、预算和可接受降级程度选择一个主路由，再保留至少一个经过测试的 fallback。

### 2. fallback 会不会造成重复扣费或重复写入？
会有风险。对有副作用的操作使用幂等键，把模型调用和业务写入分开；重试前记录 `request_id` 与 `attempt`，不要对支付、发信等动作直接重放。

### 3. 福利分组能用于 AI 客服吗？
不建议把不可预测的低稳定性路径作为客服唯一链路。客服应优先稳定官方分组，福利分组更适合可排队、可重跑的离线任务。

### 4. 如何从官方 API 迁移到网关？
先将 SDK 的 `base_url`、API key 和 model 配置化，增加超时、日志和错误分类，再灰度一个 feature；确认指标稳定后再扩大流量。

### 5. 从哪里获取接入资料？
官网：https://viralapi.ai；GitHub：https://github.com/sxl7530-hashs/viralapi-examples；GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/；FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html。

价格按场景和稳定性选择：福利分组官方 1.5 折、官转分组官方 6 折、稳定官方分组官方 8 折。联系邮箱 miutayoung@gmail.com，Telegram viral_8866，WeChat viral_8866。
