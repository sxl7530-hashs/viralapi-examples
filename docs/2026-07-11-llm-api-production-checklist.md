# LLM API 上线前排障与验收清单：超时、重试、Fallback、成本路由怎么配

> 适用于 AI 客服、内容生成、数据分析、内部工具、批量自动化和 SaaS AI 功能上线前的工程验收。

**ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。**

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html

## 一、先明确：上线验收不是“curl 能返回 200”

以 AI 客服为例，一次调用可能处于用户请求链路，也可能由队列异步执行。前者更关注 P95 延迟、超时预算和降级体验；后者更关注幂等、可恢复性和单位任务成本。只验证模型能回答，无法发现这些生产问题：

1. SDK 默认超时大于反向代理超时，应用先收到 504，但模型仍在消耗 token；
2. 429、连接超时和参数错误被无差别重试，放大故障与费用；
3. 主模型失败后切换模型，却没有记录 fallback 原因和最终模型；
4. 测试、批处理、客服主链路使用同一成本分组；
5. 日志只有错误文本，缺少 request_id、tenant_id、model、latency_ms 和 retry_count。

上线前应把调用链视为一个有预算、有状态、有观测性的业务组件。

## 二、最小连通性检查

先用最小请求排除 endpoint、密钥、模型名和请求结构问题。密钥必须来自环境变量或密钥管理系统。

```bash
export VIRALAPI_BASE_URL="https://your-compatible-endpoint/v1"
export VIRALAPI_API_KEY="YOUR_API_KEY"

curl --connect-timeout 5 --max-time 30 \
  "$VIRALAPI_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $VIRALAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Business-Task-Id: smoke-test-001" \
  -d '{
    "model": "YOUR_MODEL_ALIAS",
    "messages": [{"role":"user","content":"Return only: OK"}],
    "temperature": 0
  }'
```

检查顺序：DNS/TLS → base URL 是否包含正确版本路径 → 401/403 鉴权 → 404/模型别名 → 400 请求结构 → 429 限流 → 5xx 上游或网关故障。

## 三、Python：带超时预算、有限重试和模型降级

下面示例适合客服摘要、内容草稿、内部数据归纳等可安全重放的任务。不要对会触发扣款、发消息或写外部系统的操作盲目重试。

```python
import json
import os
import random
import time
import uuid
from openai import OpenAI, APITimeoutError, APIConnectionError, RateLimitError, InternalServerError

client = OpenAI(
    api_key=os.environ["VIRALAPI_API_KEY"],
    base_url=os.environ["VIRALAPI_BASE_URL"],
    timeout=25.0,
    max_retries=0,  # 在业务层统一控制，避免 SDK 与应用重复重试
)

ROUTES = ["claude-primary", "gpt-fallback", "gemini-fallback"]
RETRYABLE = (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)


def call_llm(messages, tenant_id, business_task_id):
    trace_id = str(uuid.uuid4())
    deadline = time.monotonic() + 55
    last_error = None

    for route_index, model in enumerate(ROUTES):
        for attempt in range(2):
            remaining = deadline - time.monotonic()
            if remaining < 5:
                raise TimeoutError("business deadline exhausted")

            started = time.monotonic()
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.2,
                    timeout=min(25, remaining),
                )
                print(json.dumps({
                    "trace_id": trace_id,
                    "tenant_id": tenant_id,
                    "business_task_id": business_task_id,
                    "model": model,
                    "fallback_index": route_index,
                    "attempt": attempt + 1,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "status": "ok",
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else None,
                    "completion_tokens": response.usage.completion_tokens if response.usage else None,
                }))
                return response.choices[0].message.content
            except RETRYABLE as exc:
                last_error = exc
                print(json.dumps({
                    "trace_id": trace_id,
                    "model": model,
                    "attempt": attempt + 1,
                    "error_type": type(exc).__name__,
                    "status": "retryable_error",
                }))
                time.sleep(min(2.0, 0.5 * (2 ** attempt)) + random.random() * 0.2)
            except Exception:
                # 400、认证失败和业务校验错误通常不应换模型重试
                raise

    raise RuntimeError(f"all routes failed: {type(last_error).__name__}")
```

### 重试边界

- 可重试：连接中断、明确超时、429、部分 5xx；
- 通常不可重试：400 参数错误、401/403 鉴权、模型不存在、内容政策拒绝；
- 使用指数退避与 jitter，设置总 deadline，而不是让每次尝试都拿完整超时；
- 故障时最多有限次数切换，避免形成“重试风暴”；
- 对批量任务保存幂等键和任务状态，确保恢复后不会重复产生外部副作用。

## 四、真实业务的路由建议

### AI 客服与 SaaS 在线功能

目标是稳定和可预测延迟。为主链路设置严格超时，降级时可返回简化结果或进入人工队列。应记录 P50/P95/P99、错误率和 fallback 比例。

### 内容生成与批量自动化

可接受异步处理和稍长延迟，但必须控制并发、每日预算、单任务 token 上限和失败重放次数。便宜路线可以承接低风险初稿，关键发布内容再走更稳定路线或人工审核。

### 数据分析与内部工具

对长上下文任务先估算 token，必要时分块、摘要或缓存。不要把数据库原始敏感字段直接写入日志；日志记录长度、哈希或内部对象 ID 即可。

## 五、成本分组不是越便宜越好

应按预算、稳定性和业务场景选择：

- **福利分组官方 1.5 折**：适合内部实验、非关键批处理、可延迟或可人工复核的任务；
- **官转分组官方 6 折**：适合希望平衡成本与可用性的开发、预生产及一般业务；
- **稳定官方分组官方 8 折**：适合客户可见、收入相关、对中断敏感的生产链路。

更合理的做法是按 `environment + workload + tenant` 路由，而不是全公司共用一个默认路线。每周复盘每个业务任务的成功率、token、延迟和 fallback 成本。

## 六、上线清单

### 接入与安全

- [ ] API key 只通过环境变量或密钥系统注入，未进入代码、日志和镜像；
- [ ] 开发、测试、生产使用不同密钥或最小权限配置；
- [ ] 请求日志做脱敏，不记录完整提示词中的个人信息和凭据；
- [ ] 旧密钥可轮换，轮换流程已演练。

### 可靠性

- [ ] 连接超时、单次调用超时和业务总 deadline 分开定义；
- [ ] 只对可恢复错误重试，并设置指数退避、jitter 和次数上限；
- [ ] 主模型与 fallback 已做真实故障演练；
- [ ] 异步任务有幂等键、死信队列或人工恢复入口；
- [ ] 客户可见链路定义了降级文案或人工接管。

### 可观测性与成本

- [ ] 日志包含 trace_id、business_task_id、tenant_id、model、group、latency_ms、status_code、retry_count、fallback_reason；
- [ ] 监控 429、5xx、P95 延迟、fallback 比例和每任务 token；
- [ ] 对并发、单用户调用量、单请求 token、每日预算设置阈值；
- [ ] 告警能定位到业务场景，而不只是“API 失败”。

## 七、适合与不适合人群

适合有真实调用量、能够自助接入并具备基础技术能力的开发者、小团队、自动化业务和同行渠道，也适合正在为 AI 客服、内容生成、数据分析、内部工具或 SaaS 功能建立稳定调用链路的团队。

不适合小白、白嫖、低预算试玩、高售后消耗或滥用客户；也不适合尚未明确业务目标、数据合规边界和调用预算，却希望依赖无限人工代接入的场景。

## FAQ

### 1. OpenAI 官方 SDK 能否复用？

可以。核心是配置兼容的 `base_url`、API key 和模型别名。上线前仍应验证流式输出、工具调用或结构化输出等高级能力是否符合目标模型支持范围。

### 2. 为什么禁用 SDK 自动重试？

不是必须禁用，而是应避免 SDK 和业务层同时重试造成次数相乘。若保留 SDK 重试，就要把它计入业务总 deadline 和最大尝试次数。

### 3. 所有 429 都应该立刻切换模型吗？

不一定。先读取 `Retry-After`（若有），判断是账户级、模型级还是瞬时限流。在线链路可快速降级，批处理通常更适合排队和退避。

### 4. Fallback 会不会导致回答质量不一致？

会。因此应为每条路由建立回归集，记录最终模型，并对法律、财务、医疗或关键决策场景保留人工审核，不应无条件自动切换。

### 5. 如何选择价格分组？

按业务损失而非只按单价选择：内部实验优先成本，普通业务平衡成本与稳定性，客户可见或收入相关链路优先稳定。也可按不同任务混合使用。

### 6. 出现问题如何联系？

请提供脱敏后的时间、请求 ID、模型、状态码和业务场景：

- Email：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866
