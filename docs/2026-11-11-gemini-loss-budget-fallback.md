# Gemini 故障时要不要切模型？先把业务损失预算写进 fallback

Gemini 请求超时、返回 429，或者成功返回了不合格结果，很多服务的第一反应是“再试一次，不行就切模型”。这条路径看似简单，却会同时改变用户等待时间、下游流量、结果质量和账单。生产环境真正需要回答的不是“能不能重试”，而是：这项业务最多能损失什么、最多等多久、最多多花多少，以及结果未知时谁负责收口。

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，可按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组。它能简化多模型接入，但应用仍需要定义自己的 deadline、重试预算、熔断、结果校验与业务幂等边界。模型 ID、base URL、可用能力和计费以账号控制台为准，本文中的阈值和代码是应用侧设计示例，不代表平台 SLA 或性能承诺。

## 先定义“损失预算”

损失预算不是一个统一的金额，而是业务愿意为一次失败承担的边界。客服首答可以接受降级措辞并转人工；批量摘要可以排队；合同字段抽取可以进入人工复核；退款、账户权限和资金操作则不能因为模型故障而自动换路执行。

| 场景 | 可接受的降级 | 失败收口 |
| --- | --- | --- |
| AI 客服首答 | 已验证候选、缩短回答 | 固定提示并转人工 |
| 报告摘要 | 缩短输出、转异步 | 返回任务 ID |
| 合同抽取 | 同一 Schema 的候选 | 有界队列、人工复核 |
| 批量内容 | 降并发、暂停消费 | 延后或死信 |
| 资金、权限、安全关键写入 | 通常不允许自动跨模型放行 | fail closed、对账或人工审批 |

定义预算时至少写明四个数字或规则：总 deadline、额外调用上限、fallback 可用容量、业务验收条件。还要确定每个请求的失败动作，以及何时将状态标记为“未知、待对账”，而不是误报成功或失败。

## 一次 deadline 管住重试与 fallback

单次 HTTP timeout 不是完整业务 deadline。一次请求可能包含排队、连接、首个 Gemini 调用、退避、第二次调用、fallback、解析和业务校验。为整个链路创建单一 deadline，并在每个阶段计算剩余时间：

```python
import time

started = time.monotonic()
deadline = started + policy.total_deadline_s
remaining_s = deadline - time.monotonic()
reserved_s = policy.fallback_reserve_s + policy.validation_reserve_s
attempt_timeout_s = min(policy.per_try_cap_s, remaining_s - reserved_s)
if attempt_timeout_s <= 0:
    return policy.exhausted_action  # queue / stop / reconcile
```

这里的 `total_deadline_s`、保留时间和单次上限应按自己的服务测量与用户承诺配置。不要把示例秒数包装成供应商保证。对实时链路，预留 fallback 和结果验证时间；异步任务则可以排队，但队列必须有界，且任务需具备可重放标识。

重试应只有一个责任人。若 SDK、业务路由层和网关都各自重试，调用次数会相乘。以 OpenAI-compatible Python SDK 为例，可以关闭 SDK 自动重试，把调用决策留在业务策略层：

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["VIRALAPI_API_KEY"],
    base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
    max_retries=0,
)
```

请按账号控制台中的实际 endpoint 与模型 ID 配置。不要在日志、异常追踪或代码仓库中输出密钥。

## Retry budget 限制故障放大

“每个请求最多重试一次”仍可能把流量翻倍。可以在服务或租户的时间窗口内限制重试总量，例如：

```text
允许重试数 = floor(窗口内初始请求数 × 10%) + 2
```

这是策略样例而非通用最佳值。实际窗口、比例和最小值要根据峰值流量、供应商限额及业务损失预算压测决定；分布式服务需要在共享存储或协调器中原子地消费预算，不能让每个进程各自拥有一份无限放大的“全局”额度。

通常只把明确的瞬态故障纳入重试候选，例如连接失败、读取超时、408、429、500、502、503、504，并要求操作可安全重放、剩余 deadline 足够、Retry-After 可遵守且 budget 仍有余额。401/403、参数或 Schema 错误应修正请求或权限，不应重试；安全拒绝不能通过切换模型绕过。

## 熔断和隔离舱是不同的保护

熔断器应按故障域隔离，例如 `model + cost_group + region`。CLOSED 状态正常采样；达到故障条件后进入 OPEN，暂时跳过目标；冷却后进入 HALF_OPEN，只放行少量探测。成功后关闭，失败则重新打开。样本窗口、失败阈值和冷却时间是应用配置，需要用故障注入验证，不能当作平台 SLA。

熔断解决“这个目标现在是否值得调用”；bulkhead 解决“一个慢目标会不会占满全体执行资源”。至少把实时主路、实时 fallback、批量任务和熔断探测分配到不同的并发槽。过载时低优先级任务应快速拒绝或进入有界队列，不能无限排队。

下面是策略判定的最小调用示例。此模块只分类决策，不会发送网络请求，也不负责实际重试；它适合放在应用路由层边界：

```bash
python3 examples/python/gemini_loss_budget_state_machine.py \
  --scenario support --status 429 --remaining-ms 4000
python3 examples/python/gemini_loss_budget_state_machine.py \
  --scenario extraction --status timeout --remaining-ms 10000
```

[状态机源码](https://github.com/sxl7530-hashs/viralapi-examples/blob/main/examples/python/gemini_loss_budget_state_machine.py)

## Fallback 返回 200 仍须验收

切换模型后，结构化输出、工具调用、引用、拒答、安全和语言表现都可能不同。主路与候选路应共用业务验证器：

```python
REQUIRED = {"vendor", "currency", "total_amount", "due_date"}

def validate_invoice(data: dict) -> dict:
    missing = REQUIRED - data.keys()
    if missing:
        raise ValueError(f"missing fields: {sorted(missing)}")
    if not isinstance(data["total_amount"], (int, float)):
        raise ValueError("total_amount must be numeric")
    if data["currency"] not in {"CNY", "USD", "EUR"}:
        raise ValueError("unsupported currency")
    return data
```

真实项目还应校验日期范围、金额精度、引用来源和领域不变量。验证失败不是传输层故障，不应计入供应商熔断的失败样本。对工具调用应实施服务端工具白名单、参数校验、权限检查、金额或次数上限和审批流程，不能信任模型生成的工具调用名称或 ID。

## 超时后的未知状态与业务幂等

客户端超时只说明客户端没按时拿到完整响应，不证明上游没有执行，也不证明没有计费。尤其是已开始流式响应、网络断开或涉及外部写操作时，应标记 `outcome_unknown`，使用业务请求 ID 查询执行记录、对账或转人工，而不是盲目重放。

幂等必须落在业务持久层：为操作建立唯一业务键与状态机，例如 `created → executing → succeeded/failed/unknown`，对重复事件做去重；只有目标外部系统明确支持幂等契约时，才依赖其幂等键。把同一个 header 传给模型 API，并不能自动让退款、发信或数据库写入变成幂等。

## 价格分组与有效结果成本

当前价格口径为：福利分组约官方 1.5 折、官转分组约官方 6 折、稳定官方分组约官方 8 折。它们是成本与稳定性分组的参考口径，不是 SLA；具体模型、价格和可用能力以账号控制台为准。

- 可重放、可排队、自动校验且低业务风险的批量任务，可评估福利分组。
- 有人工复核的内部工具、数据分析和自动化任务，可评估官转分组。
- 客户可见客服、付费 SaaS 核心功能，可优先评估稳定官方分组，并配置独立 fallback 容量。

分组选择要看预算、业务后果、稳定性和真实压测。成本不要只按标称调用价比较，应计算：

```text
单位有效结果成本 = (主路 + 重试 + fallback + 复核 + 对账成本) / 去重后验收通过的结果数
```

监控至少覆盖主路和最终成功率、fallback 率、`degraded` 标记、breaker 状态与持续时间、retry budget 消耗、总尝试次数、并发舱占用、deadline 耗尽、Schema 验证失败、未知状态数量、P95/P99 及单位有效结果成本。保留 request ID、场景、模型、成本分组、尝试序号和错误类别；不要记录 API key、完整敏感 prompt 或个人信息。

## FAQ

### 1. Gemini 返回 429，应该立即 fallback 吗？

不一定。检查 Retry-After、故障是否属于租户额度、剩余 deadline、全局 retry budget 和熔断状态。偶发且可安全重放时可有限重试；持续故障或预算不足时转已验证候选、排队或拒绝。

### 2. 熔断器已经存在，为什么还要 retry budget？

熔断需要观察窗口，打开前并发请求可能已经各自重试。Retry budget 限制窗口内额外流量，熔断跳过已知故障目标，两者保护的阶段不同。

### 3. timeout 是否代表请求没有执行或不会收费？

不代表。响应丢失、客户端取消、上游执行和计费状态彼此独立。记录未知状态并通过请求记录或账单对账，不要假设可以安全重放。

### 4. fallback 返回 HTTP 200 是否算成功？

不算。必须通过相同的 Schema、安全、权限和领域校验；不合格结果不能进入业务写入，并应记录降级或转人工。

### 5. 三种价格分组怎么选？

按预算、业务失败后果、稳定性、压测与单位有效结果成本选：低风险批量可评估福利分组，有复核的内部场景可评估官转，客户关键链路可评估稳定官方分组。具体价格与能力以账号控制台为准。

## 适合与不适合的团队

适合有真实调用量、能自助接入并具备基础工程能力的开发者、小团队、SaaS 团队和自动化业务；常见场景包括 AI 客服、内容生成、数据分析、内部工具和批量自动化。不适合只想白嫖或低预算试玩、无法承担基本排障的用户。涉及医疗、资金、安全关键决策，或强制要求特定数据驻留、单一供应商合同 SLA 的系统，需要另行满足领域合规、合同和人工审批要求，不能仅依赖多模型路由。

## 资料与联系

- 官网：https://viralapi.ai
- GitHub 示例仓库：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- Email：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866