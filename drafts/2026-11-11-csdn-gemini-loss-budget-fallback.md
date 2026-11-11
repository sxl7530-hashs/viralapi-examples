# Gemini API 超时、429 后如何 fallback：一套可落地的 Loss Budget 状态机

> 本文面向需要把 Gemini 接入生产系统的 Python 开发者。重点不是再写一个 `try/except`，而是把总超时、重试预算、熔断、并发隔离和业务幂等放到同一个路由策略里。

ViralAPI 是面向开发者、小团队和自动化业务的 OpenAI-compatible 多模型 API 网关，可按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组。模型 ID、endpoint、能力和计费请以账号控制台为准。本文所有秒数、阈值和代码都是应用侧示例，不代表平台 SLA。

## 1. 为什么“失败就重试”会让事故变大

假设客服高峰有 100 个并发请求，Gemini 发生超时。每个请求最多重试一次，理论下游流量就可能从 100 增加到 200；如果再加一次 fallback，连接池、线程池和下游限额都会继续承压。更糟的是，超时不代表上游没有执行，写操作可能已经发生。

先按业务定义损失预算：

| 业务 | 可降级结果 | 不能自动做的事 |
| --- | --- | --- |
| 客服首答 | 短回答、已验证候选、转人工 | 伪造事实或承诺 |
| 报告摘要 | 转异步、返回任务 ID | 把未校验摘要写入结论 |
| 合同抽取 | 进入人工复核 | 未通过 Schema 就入账 |
| 批量内容 | 暂停消费、延后 | 挤占实时容量 |
| 退款、权限、资金写入 | fail closed、对账 | 只凭模型输出执行 |

## 2. OpenAI-compatible 客户端只负责一次调用

业务路由器应成为唯一的重试所有者，关闭 SDK 内置重试，避免多层重试叠加：

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["VIRALAPI_API_KEY"],
    base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
    max_retries=0,
)
```

不要把密钥放进日志。请求的模型名、参数和能力要先在控制台确认；OpenAI-compatible 只描述接入形态，不等于所有模型都支持完全相同的工具、流事件或结构化输出。

总 deadline 必须覆盖排队、网络、退避、fallback、解析和业务验证：

```python
import time

deadline = time.monotonic() + scenario.total_deadline_s
remaining = deadline - time.monotonic()
reserve = scenario.fallback_reserve_s + scenario.validation_reserve_s
per_try = min(scenario.per_try_cap_s, remaining - reserve)
if per_try <= 0:
    return "queue" if scenario.allow_queue else "stop"
```

示例中的时间不是平台承诺。实时接口应给验证和输出留余量；批处理可以排队，但队列长度和等待时间必须有限。

## 3. Retry budget、Retry-After 与错误分类

可用一个窗口级预算限制额外尝试：

```text
allowed_retries = floor(initial_requests * 0.10) + 2
```

这只是起始策略，应通过自己的峰值压测调整。100 个初始请求约允许 12 次额外尝试，而不是每个请求都拥有一次重试额度。分布式服务要用共享原子计数，并按租户或故障域隔离。

仅在以下条件同时满足时重试：故障是瞬态、请求可安全重放、遵守 `Retry-After`、retry budget 有余额，且剩余 deadline 足够。连接失败、读取超时、408、429、500、502、503、504 可进入候选；401/403、400/422、Schema 错误和安全拒绝应停止并修正原因，不能靠换模型规避。

## 4. 熔断与 bulkhead 的最小分工

熔断器按 `model + cost_group + region` 记录状态：CLOSED 正常调用，OPEN 冷却期跳过，HALF_OPEN 只放少量探测。熔断回答“是否继续打这个目标”；retry budget 回答“整个窗口还能多打多少次”。两者不能互相替代。

并发资源还应拆分为实时主路、实时 fallback、批量任务、HALF_OPEN 探测四个隔离舱。否则主路慢请求占满共享 semaphore 后，健康 fallback 也无法执行。容量不足时快速拒绝低优先级工作或进入有界队列，不要用无限队列掩盖故障。

仓库提供一个不发网络请求的状态机，可用来检查各种边界：

```bash
python3 examples/python/gemini_loss_budget_state_machine.py \
  --scenario support --status 429 --remaining-ms 4000
python3 examples/python/gemini_loss_budget_state_machine.py \
  --scenario support --status timeout --remaining-ms 3000
```

源码：[gemini_loss_budget_state_machine.py](https://github.com/sxl7530-hashs/viralapi-examples/blob/main/examples/python/gemini_loss_budget_state_machine.py)

## 5. 200 不等于可写入业务

Fallback 需要和主路执行同一套业务验收：

```python
REQUIRED = {"vendor", "currency", "total_amount", "due_date"}

def validate(data):
    missing = REQUIRED - data.keys()
    if missing:
        raise ValueError(f"missing: {sorted(missing)}")
    if not isinstance(data["total_amount"], (int, float)):
        raise ValueError("amount must be numeric")
    if data["currency"] not in {"CNY", "USD", "EUR"}:
        raise ValueError("unsupported currency")
    return data
```

还要校验领域不变量、引用、日期和金额精度。工具调用必须由服务端做白名单、权限、参数和审批校验。验证失败是业务契约失败，不应直接当作传输故障去打开供应商熔断。

## 6. 超时、流式中断与幂等

出现 timeout、断连接或已经收到部分流时，状态应为 `unknown`，而不是“肯定没执行”。通过业务请求 ID 查询执行记录、账单或外部系统状态；只有确认可安全重放才重试。

业务幂等需要数据库唯一键和持久化状态机，例如 `created -> executing -> succeeded/failed/unknown`。一个传给模型接口的 `Idempotency-Key` 不能单独保证退款、发信或数据库写入不重复。

## 7. 价格与监控

当前可参考的价格分组为：福利约官方 1.5 折、官转约官方 6 折、稳定官方约官方 8 折。福利适合可重放、低风险批处理；官转可评估内部工具和有人复核的自动化；客户可见客服与 SaaS 核心链路可优先评估稳定官方分组。分组价格不是 SLA，最终以账号控制台为准。

建议看单位有效结果成本，而非只看单次调用价：

```text
(主路 + 重试 + fallback + 人工复核 + 对账) / 去重且验收通过的结果数
```

日志至少包含 request ID、模型、成本分组、attempt、fallback_index、剩余 deadline、retry budget、breaker、`degraded` 和错误类别；不要记录密钥、完整敏感 prompt 或个人信息。

## FAQ

### Gemini 429 后一定要 fallback 吗？

不一定。先看 Retry-After、剩余 deadline、预算和熔断状态。偶发限流可以做有限重试；持续故障则切换已回归验证的候选、排队或拒绝。

### 为什么每请求一次重试还不够？

因为全局流量会放大。窗口级 retry budget 能限制额外尝试总量，避免每个并发请求同时重试。

### 熔断打开后还需要 fallback 并发池吗？

需要。熔断只跳过已知故障目标，独立 fallback 池还要防止主路慢请求占满所有资源。

### timeout 后可以立即重放吗？

如果执行结果未知，不能直接重放写操作。先查询、对账或进入人工流程；只对业务上可安全重放的读或纯计算任务重试。

### 怎么选价格分组？

按失败后果、预算、压测、稳定性和单位有效结果成本选，不要只看折扣。

## 资料、链接与联系

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- Email：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866