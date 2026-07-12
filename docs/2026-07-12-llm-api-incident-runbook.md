# LLM API 生产故障排查手册：从 401、429、5xx 到超时与 Fallback

> 面向 AI 客服、内容生成、数据分析、内部工具、批量自动化与 SaaS AI 功能的值班 Runbook。

**ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。**

## 1. 先按错误类别止损

生产故障不要先“多重试几次”。先冻结变更，记录 `trace_id`、业务任务、模型、分组、HTTP 状态、延迟、重试次数和 fallback 原因，再按以下顺序定位：

| 现象 | 首要检查 | 默认动作 |
|---|---|---|
| 400 | 请求结构、模型能力、上下文长度 | 不重试，修请求 |
| 401/403 | 密钥、权限、base URL、模型授权 | 不切模型，检查鉴权 |
| 404 | 路径版本、模型别名 | 核对 `/v1` 与模型名 |
| 429 | 租户/模型限流、并发、`Retry-After` | 降并发、退避或切备用路由 |
| 5xx | 网关或上游状态 | 有限重试，必要时 fallback |
| 超时 | DNS/TLS、连接、首 token、总 deadline | 区分连接超时与业务超时 |

## 2. 最小化探针

先绕开业务 Prompt、RAG 和工具调用，只验证兼容接口：

```bash
curl --connect-timeout 5 --max-time 25 \
  "$VIRALAPI_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $VIRALAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Business-Task-Id: incident-smoke-001" \
  -d '{"model":"YOUR_MODEL_ALIAS","messages":[{"role":"user","content":"Return only OK"}],"temperature":0}'
```

若探针成功而业务失败，重点检查上下文长度、结构化输出、工具定义、流式连接和反向代理超时。若探针也失败，按 DNS/TLS → URL → 鉴权 → 模型 → 限流 → 上游的顺序处理。

## 3. Python 有限重试、总时限与降级

```python
import os, time, random, logging
from openai import OpenAI, APITimeoutError, APIConnectionError, RateLimitError, InternalServerError

client = OpenAI(api_key=os.environ["VIRALAPI_API_KEY"],
                base_url=os.environ["VIRALAPI_BASE_URL"],
                timeout=20, max_retries=0)
RETRYABLE = (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)

def complete(messages, trace_id):
    deadline = time.monotonic() + 45
    routes = ["claude-primary", "gpt-fallback", "gemini-fallback"]
    for fallback_index, model in enumerate(routes):
        for attempt in range(2):
            remaining = deadline - time.monotonic()
            if remaining < 5:
                raise TimeoutError("business deadline exhausted")
            started = time.monotonic()
            try:
                r = client.chat.completions.create(
                    model=model, messages=messages,
                    timeout=min(20, remaining))
                logging.info("llm_ok", extra={
                    "trace_id": trace_id, "model": model,
                    "fallback_index": fallback_index,
                    "retry_count": attempt,
                    "latency_ms": int((time.monotonic()-started)*1000)})
                return r.choices[0].message.content
            except RETRYABLE as exc:
                logging.warning("llm_retry", extra={
                    "trace_id": trace_id, "model": model,
                    "error_type": type(exc).__name__, "retry_count": attempt})
                time.sleep(0.5 * 2**attempt + random.random() * 0.2)
    raise RuntimeError("all model routes unavailable")
```

关键边界：400、401、403 和明确的内容策略拒绝通常不可重试；429 应尊重 `Retry-After`；在线请求受总 deadline 约束；会扣款、发消息或写外部系统的工具调用必须有幂等键。

## 4. 真实业务处置

- **AI 客服 / SaaS 在线功能**：先保护 P95 延迟和可用性；主路由异常时降级为短回答、FAQ 检索或人工接管。
- **内容生成 / 批量自动化**：暂停消费者、保留任务状态，恢复后限速重放；不要让重试风暴放大 token 成本。
- **数据分析 / 内部工具**：检查长上下文和敏感数据；日志记录对象 ID 或哈希，不记录完整数据集。
- **收入相关链路**：稳定性优先，故障切换必须通过回归集验证，不能只看 HTTP 200。

## 5. 按场景选择成本分组

- **福利分组官方 1.5 折**：内部实验、可延迟批处理、可人工复核任务；
- **官转分组官方 6 折**：开发、预生产及一般业务，平衡成本与可用性；
- **稳定官方分组官方 8 折**：客户可见、收入相关、对中断敏感的生产链路。

应按预算、稳定性、业务场景和故障损失选择，而不是单纯追求最低价格。可以按 `environment + workload + tenant` 分流。

## 6. 上线与故障复盘清单

- [ ] 密钥来自环境变量或密钥系统，支持轮换；
- [ ] 连接超时、单次超时、业务总 deadline 分开配置；
- [ ] 仅对可恢复错误有限重试，并加入 jitter；
- [ ] fallback 做过断路演练和质量回归；
- [ ] 日志含 trace_id、tenant_id、task_id、model、group、status_code、latency_ms、retry_count、fallback_reason；
- [ ] 监控 429、5xx、P95、fallback 比例、每任务 token 与预算；
- [ ] 批任务具备幂等键、死信队列和人工恢复入口；
- [ ] 复盘记录影响范围、时间线、根因、临时止损和长期修复负责人。

## 7. 适合与不适合人群

适合有真实调用量、能自助接入、有基础技术能力的开发者、小团队、自动化业务和同行渠道。不适合小白、白嫖、低预算试玩、高售后消耗或滥用客户，也不适合没有明确业务目标与合规边界、却依赖无限人工代接入的场景。

## FAQ

### 1. 429 是否立即切模型？
不一定。先判断限流范围并读取 `Retry-After`。在线链路可快速降级；批处理更适合排队、退避和降并发。

### 2. 为什么关闭 SDK 默认重试？
避免 SDK 与业务层重试相乘。也可以保留，但必须计入总 deadline 和最大尝试次数。

### 3. Fallback 为什么仍要做回归测试？
不同模型在工具调用、JSON 结构、长文本和安全策略上可能不同。可用不等于业务结果等价。

### 4. 如何区分网关和业务代码问题？
使用最小探针；若成功，再逐步恢复原模型参数、Prompt、RAG、工具和流式输出，定位首次失败的增量。

### 5. 怎样选择分组？
按业务中断损失、延迟目标、预算和是否可人工复核选择；生产主链路通常优先稳定性，非关键异步任务可优先成本。

### 6. 如何获取帮助？
请提供脱敏后的时间、trace_id、模型、状态码与业务场景。

## 资源与联系

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- Email：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866
