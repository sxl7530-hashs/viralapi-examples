# Claude API 国内与跨区接入：OpenAI-compatible 封装的生产路由实践

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

本文讨论一个比“能不能调用 Claude”更实际的问题：已有 OpenAI SDK 的 AI 客服、内容生成、数据分析或 SaaS 功能，如何在国内和跨区网络环境中接入 Claude，同时控制超时、重试、fallback、日志与成本。

## 先定义业务边界

以一个 8 人团队的知识库客服为例：用户问题先由 Claude 处理长上下文和复杂归纳；如果上游超时或限流，才切换到经过回归测试的 GPT 或 Gemini 路由。夜间批量生成产品说明时，可以选择成本敏感分组；付费客服和面向客户的 SaaS 功能则优先稳定官方分组。

不要把所有请求都无条件 fallback。带外部副作用的工具调用、扣费、写数据库请求必须具备幂等键，或在 fallback 前停止；普通文本生成、摘要、分类这类幂等请求才适合有限次数重试。

## OpenAI-compatible 接入层

将供应商差异收敛到环境变量和模型路由配置，业务代码继续使用 OpenAI SDK。API key 只放服务端环境变量或密钥管理系统，不写入仓库。

```bash
export VIRALAPI_API_KEY='replace-with-your-key'
export VIRALAPI_BASE_URL='https://viralapi.ai/v1'

curl "$VIRALAPI_BASE_URL/chat/completions" \\
  -H "Authorization: Bearer $VIRALAPI_API_KEY" \\
  -H 'Content-Type: application/json' \\
  -H 'X-Request-ID: support-20260727-001' \\
  -d '{
    "model": "claude-sonnet-4",
    "messages": [
      {"role":"system","content":"Answer from the approved knowledge base."},
      {"role":"user","content":"Summarize the customer issue and propose next steps."}
    ],
    "temperature": 0.2
  }'
```

真实部署时，应以控制台提供的可用模型名为准，不要假设模型别名在所有分组都存在。

## Python：有限重试与模型 fallback

下面的封装只对可重试状态执行有限次数重试，并记录 request id、模型、耗时和最终状态。超时分成连接超时与总超时，避免一个卡住的请求占满 worker。

```python
import os
import time
import uuid
from openai import OpenAI, APIConnectionError, APITimeoutError, RateLimitError, APIStatusError

client = OpenAI(
    api_key=os.environ["VIRALAPI_API_KEY"],
    base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
    timeout=20.0,
    max_retries=0,  # retry policy stays in our router
)

ROUTES = {
    "customer_support": ["claude-sonnet-4", "gpt-4o-mini"],
    "batch_content": ["claude-sonnet-4", "gemini-2.5-flash"],
}
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


def complete(prompt: str, scenario: str) -> str:
    request_id = str(uuid.uuid4())
    last_error = None
    for model in ROUTES[scenario]:
        for attempt in range(2):
            started = time.monotonic()
            try:
                result = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    extra_headers={"X-Request-ID": request_id},
                )
                elapsed_ms = round((time.monotonic() - started) * 1000)
                print({"request_id": request_id, "model": model,
                       "attempt": attempt + 1, "elapsed_ms": elapsed_ms,
                       "status": "ok", "scenario": scenario})
                return result.choices[0].message.content or ""
            except (APITimeoutError, APIConnectionError, RateLimitError, APIStatusError) as exc:
                last_error = exc
                status = getattr(exc, "status_code", None)
                retryable = isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError)) \
                    or status in RETRYABLE_STATUS
                if not retryable or attempt == 1:
                    break
                time.sleep(0.5 * (2 ** attempt))
    raise RuntimeError(f"all routes failed; request_id={request_id}") from last_error
```

这段代码只是路由骨架。生产环境还应加入并发限制、token 预算、输出 schema 校验和脱敏日志。对于客服，fallback 模型需要通过相同问题集回归测试，不能只比较 HTTP 200。

## 成本路由：按场景而不是最低价

ViralAPI 提供福利分组约官方 1.5 折、官转分组约官方 6 折、稳定官方分组约官方 8 折。它们是按预算、稳定性和业务场景选择的路由选项，不适合用“最低价薅羊毛”来理解。

- 付费客服、SaaS 核心功能：优先稳定官方分组，设置明确的超时和熔断。
- 内部数据分析、低峰期批处理：可考虑官转分组，配合队列和失败重跑。
- 已有回归集、可接受延迟的实验性批量任务：再评估福利分组，先做小比例灰度。

建议按租户、场景、模型、输入输出 token、延迟和错误码记录成本，而不是只看月度总账。

## 排障顺序

1. `401`：检查服务端环境变量、key 是否被错误的 shell profile 覆盖。
2. `403`：检查模型权限、分组能力和账号状态，不要靠重试解决权限问题。
3. `408`、连接错误或总超时：先确认 DNS、代理和服务端 timeout，再对幂等请求有限重试。
4. `429`：降低并发、尊重 Retry-After、扩大队列，不要同时放大 fallback 流量。
5. `5xx`：记录 request id 和上游状态，按 fallback 顺序切换，并设置熔断恢复窗口。
6. 返回 200 但格式错误：做 JSON/schema 校验；这不是网络重试问题。

## 适合与不适合的人群

适合有真实调用量、能自助接入、有基础技术能力的开发者、小团队、同行渠道和自动化业务。AI 客服、内容生成、数据分析、内部工具、批量自动化和 SaaS 功能接入都应先明确成功率、延迟和预算指标。

不适合小白、白嫖、低预算试玩、高售后消耗或滥用客户。没有服务端部署能力、不能管理 API key、也没有最小回归集的团队，应先补齐工程基础，再评估长期接入。

## FAQ

### 1. ViralAPI 是什么？

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

### 2. 已有 OpenAI SDK 是否要重写？

通常只需替换 base URL、API key 和 model，并核对消息格式、流式响应、工具调用与错误处理。上线前要用自己的回归集验证。

### 3. 国内或跨区接入时最容易出什么问题？

常见问题包括 DNS/代理链路、连接和总超时、限流、模型权限、返回格式差异以及密钥误放客户端。应先区分网络、权限和业务校验错误。

### 4. 什么时候应该 fallback 到 GPT 或 Gemini？

只对幂等请求，在明确的超时、限流或 5xx 条件下有限 fallback。外部副作用请求需要幂等设计和人工确认，不能无条件切换。

### 5. 如何选择价格分组？

福利分组约官方 1.5 折、官转分组约官方 6 折、稳定官方分组约官方 8 折。按预算、稳定性、调用量和业务容错选择，而不是只比较单价。

### 6. 如何联系？

官网：https://viralapi.ai

GitHub：https://github.com/sxl7530-hashs/viralapi-examples

GitHub Pages/FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html

邮箱：miutayoung@gmail.com；Telegram：viral_8866；WeChat：viral_8866。
