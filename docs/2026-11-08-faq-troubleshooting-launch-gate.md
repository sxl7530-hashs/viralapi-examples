# LLM API 上线前 FAQ 与排障清单：从 401/429/超时到可回滚的 AI 业务接入

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

本文面向准备把模型调用接入 AI 客服、内容生成、数据分析、内部工具、批量自动化或 SaaS 功能的小团队。重点不是“请求能返回 200”就上线，而是把错误分类、总截止时间、fallback、预算和人工接管写成可验证的上线门槛。

## 一、先定义上线边界

一次模型调用至少要有 `operation_id`、脱敏 `tenant_id`、`scenario`、`model`、`cost_group`、`attempt`、`deadline_ms`、`status`、`upstream_request_id` 和 `validation_passed`。日志不得包含 API key、完整提示词、客户隐私或上游错误正文。

HTTP 200 只是传输层成功。业务层还要验证 choices 是否存在、输出是否为空或截断、是否通过 JSON/schema/禁词/权限校验。涉及退款、发信、改库存等外部副作用时，模型输出只能进入人工或确定性业务校验，不能直接执行。

## 二、用一个离线 gate 阻止误上线

下面的 Python 示例不访问网络，只把真实响应映射为 `retry`、`fallback`、`manual` 或 `release`。它适合放进 CI 或上线评审工具，避免把 401 当成重试、把未知超时当成可安全重放。

```python
from dataclasses import dataclass
from enum import Enum

class Decision(str, Enum):
    RELEASE = "release"
    RETRY = "retry"
    FALLBACK = "fallback"
    MANUAL = "manual"

@dataclass(frozen=True)
class Result:
    decision: Decision
    reason: str

def classify(*, status: int | None, timed_out: bool, idempotent: bool,
             deadline_left_ms: int, validation_passed: bool,
             fallback_tested: bool) -> Result:
    if timed_out:
        # 请求可能已经被上游执行；先按未知结果对账。
        return Result(Decision.MANUAL, "unknown_outcome_reconcile_by_operation_id")
    if status in (401, 403, 400):
        return Result(Decision.MANUAL, "fix_contract_or_authorization")
    if status == 429 or status in (502, 503, 504):
        if idempotent and deadline_left_ms > 500 and fallback_tested:
            return Result(Decision.FALLBACK, "one_bounded_alternate_attempt")
        if idempotent and deadline_left_ms > 500:
            return Result(Decision.RETRY, "one_bounded_retry")
        return Result(Decision.MANUAL, "no_safe_budget_for_retry")
    if status == 200 and not validation_passed:
        return Result(Decision.MANUAL, "business_validation_failed")
    if status == 200:
        return Result(Decision.RELEASE, "transport_and_business_checks_passed")
    return Result(Decision.MANUAL, "unclassified_failure")

if __name__ == "__main__":
    print(classify(status=429, timed_out=False, idempotent=True,
                   deadline_left_ms=2000, validation_passed=False,
                   fallback_tested=True))
```

生产服务还需要把入口排队、连接建立、上游调用、结果校验和响应写回放进同一个总 deadline。SDK 的 read timeout 不等于业务墙钟截止；`Promise.race` 或协作式取消也不代表上游一定停止执行或停止计费。重试只能由一层负责，SDK、反向代理和业务代码同时重试会放大 429。

## 三、排障决策表

- **400**：检查模型 ID、消息结构、token 上限和 schema；不要换模型掩盖参数错误。
- **401/403**：检查 API key、模型权限、账户授权和 endpoint；这是配置/合同问题，不是重试问题。
- **429**：区分余额、RPM/TPM、租户预算和短时拥塞；尊重 `Retry-After`，超过剩余 deadline 就排队或人工处理。
- **502/503/504**：只有幂等任务、预算足够且备用模型已回归时，才允许一次有限重试或 fallback。
- **超时**：如果请求已发送，状态可能未知；用 `operation_id` 对账后再决定是否重放，外部写操作默认人工确认。
- **200 但结果异常**：检查空 choices、截断、格式校验、引用权限和业务禁词；不要把传输成功当成业务成功。

## 四、成本分组与业务选型

福利分组官方 1.5 折、官转分组官方 6 折、稳定官方分组官方 8 折。应按预算、稳定性和业务场景选择，而不是以最低价格替代 SLA：可重放且有人工复核的批量内容可评估福利分组；内部工具和数据分析可评估官转分组；客户可见客服和付费 SaaS 核心链路优先评估稳定官方分组。具体模型、权限和计费以控制台为准。

上线 gate 至少覆盖：真实业务样例验收率、P95 延迟、429 比例、fallback 成功率、每份合格结果的总成本（含重试和人工复核）、敏感日志扫描、回滚负责人和人工接管路径。小流量灰度按租户与场景放量，保留策略版本。

## 适合与不适合人群

适合有真实调用量、能自助接入、有基础技术能力的小团队、开发者、SaaS 团队和同行渠道。不适合小白、白嫖、低预算试玩、高售后消耗或滥用客户。需要合同 SLA、特定数据驻留或监管保证的业务，应先核实条件再选择路线。

## FAQ

### 1. 401 可以自动切到另一个模型吗？

通常不应自动切换。先核对 key、模型权限和 endpoint；否则 fallback 只会掩盖配置错误。

### 2. 429 是否一定要重试？

不一定。读取 Retry-After，确认剩余 deadline、租户预算和任务幂等性；不满足条件就排队或人工处理。

### 3. 超时后为什么不能立即重放？

请求可能已经在上游执行。先按 operation_id 对账，尤其是有外部副作用的任务。

### 4. 200 为什么还要做业务校验？

200 只表示收到 HTTP 响应；输出可能为空、截断、格式错误或没有通过权限与事实检查。

### 5. 小团队怎样选价格分组？

按预算、稳定性和场景选择，不把福利价格当作生产 SLA；先做小流量回归和成本/质量对账。

官网：https://viralapi.ai

GitHub：https://github.com/sxl7530-hashs/viralapi-examples

GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/

FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html

深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html

邮箱：miutayoung@gmail.com；Telegram：viral_8866；WeChat：viral_8866。
