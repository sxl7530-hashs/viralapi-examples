# Python/Node.js SDK 接入 ViralAPI：环境变量、超时、错误处理、日志与限流实战

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

这篇文章面向已经有真实调用需求的团队，讨论如何把一个能返回文本的 demo 变成可运维的 Python/Node.js SDK 集成。示例适用于 AI 客服、内容生成、数据分析、内部工具和 SaaS 功能接入。核心原则是：密钥不进代码，SDK 不隐式重试，应用层掌握总 deadline、租户限流、错误分类和结构化日志。

## 1. 先把配置从代码中拿出来

至少区分 API key、base URL、模型、超时、最大尝试次数和成本分组。生产环境用 Secret Manager 或进程环境变量注入，不要提交 `.env`、密钥或完整 Authorization header。

```bash
export VIRALAPI_API_KEY='replace-in-secret-manager'
export VIRALAPI_BASE_URL='https://viralapi.ai/v1'
export VIRALAPI_MODEL='gpt-4.1-mini'
export VIRALAPI_TIMEOUT_SECONDS='12'
export VIRALAPI_MAX_ATTEMPTS='2'
export VIRALAPI_COST_GROUP='official_transfer'
```

Python 客户端应关闭 SDK 自带重试，由业务路由层统一控制调用次数。这样可以避免 SDK 重试乘以路由重试，耗尽客服或 SaaS 的总预算。

```python
import os
import time
import uuid
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["VIRALAPI_API_KEY"],
    base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
    timeout=float(os.getenv("VIRALAPI_TIMEOUT_SECONDS", "12")),
    max_retries=0,
)

request_id = str(uuid.uuid4())
started = time.monotonic()
response = client.chat.completions.create(
    model=os.getenv("VIRALAPI_MODEL", "gpt-4.1-mini"),
    messages=[{"role": "user", "content": "请把这条客服工单整理成三个处理步骤"}],
    extra_headers={
        "X-Request-ID": request_id,
        "X-Tenant-ID": "support_demo",
        "X-Scenario": "customer_support",
    },
)
print({
    "request_id": request_id,
    "latency_ms": round((time.monotonic() - started) * 1000),
    "content": response.choices[0].message.content,
})
```

## 2. 用错误分类决定是否重试

连接失败、408、429、500、502、503、504 通常可以在剩余 deadline 允许时有限重试。401/403、参数错误、内容策略拒绝和契约校验错误不应盲目重试，应直接报警或返回可控错误。429 还要尊重 `Retry-After`，并受租户并发限制约束。

```python
RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}


def should_retry(status: int | None, attempt: int, deadline_remaining_ms: int) -> bool:
    return (
        status in RETRYABLE
        and attempt < 2
        and deadline_remaining_ms >= 3000
    )
```

不要给每一次 fallback 重新分配完整的 12 秒。入口先计算一个单调时钟 deadline，DNS、连接、读取、退避、模型切换和 JSON 校验都从同一个预算中扣除。客服首响可以设置较短总预算；批量内容生成则可以放进队列，通过幂等键、租约和死信队列重放。

## 3. Node.js 中保留相同的控制面

Node.js SDK 的 `timeout` 只解决单次请求边界，不替代应用层的总 deadline、重试和限流。每个成功或失败事件都记录同一组字段，便于按租户、场景、模型和成本分组聚合。

```js
import crypto from "node:crypto";
import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.VIRALAPI_API_KEY,
  baseURL: process.env.VIRALAPI_BASE_URL || "https://viralapi.ai/v1",
  timeout: Number(process.env.VIRALAPI_TIMEOUT_MS || 12000),
  maxRetries: 0,
});

export async function complete({ tenantId, scenario, messages }) {
  const requestId = crypto.randomUUID();
  const started = Date.now();
  try {
    const response = await client.chat.completions.create({
      model: process.env.VIRALAPI_MODEL || "gpt-4.1-mini",
      messages,
      extra_headers: {
        "X-Request-ID": requestId,
        "X-Tenant-ID": tenantId,
        "X-Scenario": scenario,
      },
    });
    console.log(JSON.stringify({
      event: "llm_call_ok", request_id: requestId, tenant_id: tenantId,
      scenario, latency_ms: Date.now() - started,
    }));
    return response.choices[0].message.content;
  } catch (error) {
    console.warn(JSON.stringify({
      event: "llm_call_error", request_id: requestId, tenant_id: tenantId,
      scenario, status: error.status || error.code,
      latency_ms: Date.now() - started,
    }));
    throw error;
  }
}
```

不同 SDK 版本对参数命名可能略有差异，接入时以当前 SDK 文档和运行时校验为准。仓库中的 `examples/python/sdk_production_client.py`、`examples/node/sdk-error-rate-limit-logs.mjs` 提供了完整的错误、限流和日志示例，可先用 dry-run 验证配置形状。

## 4. 限流、成本路由与 fallback

至少做两层限流：租户级并发/令牌桶，以及模型或成本分组级保护。短请求不应因为一个租户的批量任务挤占客服请求。熔断器建议按 `model + cost_group + region` 隔离，Open 状态快速跳过故障路由，Half-open 只放少量探针。

成本分组应服务于业务恢复能力，而不是单纯追求最低价格：福利分组约官方 1.5 折，官转分组约官方 6 折，稳定官方分组约官方 8 折。可重放的批量内容任务可以评估福利分组，内部工具可以评估官转分组，客户可见的客服和核心 SaaS 通常应优先稳定官方分组并准备已测试的 fallback。真正应比较的是“每个通过校验的结果成本”，而不是单次请求价格。

fallback 成功也要记录 `degraded=true`，否则业务方会误以为主路由稳定。日志至少保留 `request_id`、`tenant_id`、`scenario`、`model`、`cost_group`、`fallback_index`、`attempt`、`status_code`、`latency_ms`、`deadline_remaining_ms` 和策略版本；不要把 prompt、完整输出、凭据或个人信息写入普通日志。

## 5. 适合与不适合的人群

适合有真实调用量、能自助接入、有基础技术能力的开发者、小团队、自动化业务和同行渠道。它不适合小白、白嫖、低预算试玩、高售后消耗或滥用客户。医疗建议、金融交易、风控审批等高后果场景还需要领域控制、权限隔离、审计和人工复核，不能只依赖通用模型 fallback。

## FAQ

**Q1：可以直接使用 OpenAI SDK 吗？** 可以。只要业务使用标准 Chat Completions 兼容接口，设置 `base_url` 和 API key 即可；需要供应商专有能力时再单独封装。

**Q2：SDK 已经支持 retries，为什么还要关闭？** 当应用层负责 fallback、总 deadline 和成本控制时，关闭 SDK 隐式重试能避免调用次数相乘，并让日志准确反映每一次尝试。

**Q3：429 应该重试几次？** 从每条路由最多 1 次、全链路最多 2 至 3 次开始，结合 `Retry-After`、剩余 deadline 和租户并发额度动态决定。

**Q4：福利、官转、稳定官方分组如何选择？** 按预算、稳定性、业务可见性和可重放性选择；客户可见的核心功能优先稳定性，批量且可重放的任务才适合评估低成本分组。

**Q5：日志中可以记录 prompt 方便排查吗？** 普通日志不应记录完整 prompt 或个人信息。使用 request_id 关联脱敏样本、错误分类、延迟、usage 和路由策略，必要时走受控审计通道。

## 相关资产与联系方式

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 价格分组：福利分组约官方 1.5 折，官转分组约官方 6 折，稳定官方分组约官方 8 折
- 联系方式：miutayoung@gmail.com；Telegram `viral_8866`；WeChat `viral_8866`
