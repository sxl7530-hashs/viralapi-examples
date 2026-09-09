---
title: "Gemini 生产级 fallback：总 deadline、有限重试、熔断与成本路由"
description: "从真实业务后果出发设计 Gemini fallback，包含全链路总 deadline、有限重试、熔断状态机、跨模型降级、结构化日志、成本路由与可运行 Python 骨架。"
date: 2026-09-09
permalink: /docs/2026-09-09-gemini-fallback-timeout-retry-circuit-breaker.html
---

# Gemini 生产级 fallback：总 deadline、有限重试、熔断与成本路由

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

本指南讨论的不是“Gemini 报错后换一个模型”这一条 `except`，而是一条有业务边界的生产链路：入口只给一个总 deadline；每次尝试从同一预算扣除时间；只有瞬态错误可以有限重试；持续故障触发熔断；fallback 必须经过输出验证；每个决策都能由日志解释；成本分组由场景、预算与稳定性共同决定。

## 1. 从真实业务后果反推策略

同一个 Gemini 超时，在不同业务里不是同一种事故。

| 业务场景 | 用户等待 | 示例总 deadline | fallback 后还要做什么 | 最终失败动作 |
| --- | --- | ---: | --- | --- |
| 电商 AI 客服首答 | 是 | 8 秒 | 检查回答非空、敏感信息与语言 | 返回固定兜底并转人工 |
| 付费 SaaS 文档摘要 | 是 | 15 秒 | 检查租户权限、长度与引用格式 | 显式告知稍后重试，不伪造结果 |
| 发票/合同结构化抽取 | 可异步 | 35 秒 | 对 JSON Schema、金额与日期做校验 | 入重试队列，超限进死信队列 |
| 营销内容批量生成 | 否 | 单任务 60 秒 | 去重、品牌词和内容安全检查 | 可重放队列，不占住 Web 请求 |
| 风控或医疗建议 | 是/否 | 依系统而定 | 需要专门模型、规则与人工复核 | fail closed，不应靠通用 fallback 自动放行 |

三个具体例子：

1. **客服大促流量**：Gemini 在峰值出现 429。每个请求重试三次会把入口流量放大四倍。正确做法是单次短退避、达到窗口阈值后熔断，并把剩余 deadline 留给已验证的 fallback；若仍失败，直接转人工。
2. **合同抽取**：Gemini 返回超时，GPT fallback 虽成功却把金额输出成字符串。网络成功不等于业务成功，必须在每个候选后重新执行同一份 schema 与领域校验，失败才可继续下一候选。
3. **SEO 批处理**：作业可重放，没必要为了低延迟购买最高稳定路径，也不应在一个 worker 中无限等待。低成本路由配合队列、幂等键、每租户并发上限和死信队列，通常比同步多次 fallback 更稳。

## 2. 参考架构：把策略、执行和验证分开

```text
Client / queue worker
        |
        | request_id, tenant_id, scenario, idempotency_key
        v
Ingress guard -------- tenant concurrency / daily budget / total deadline
        v
Policy router -------- candidate model + cost group + per-try cap
        v
Execution guard ------ retry classifier / jitter / circuit breaker
        v
ViralAPI OpenAI-compatible endpoint -> Gemini primary
        | transient failure or invalid output
        +----------------------------> GPT/Claude fallback
        v
Output validator ----- schema / safety / business invariants
        v
Result or explicit degraded response
        |
        +---- structured events, metrics, traces, cost ledger
```

边界应明确：

- **策略层**按 `tenant_id + scenario` 选择候选、成本分组、deadline 和最大尝试数。
- **执行层**统一关闭 SDK 隐式重试，维护总 deadline、退避和熔断状态。
- **验证层**不因切换模型而改变业务契约；工具调用、JSON 和安全规则都要重验。
- **存储层**只在结果验证通过后写库。带副作用的工具调用需要幂等键，不能盲目重放。
- **观测层**记录决策元数据，不默认记录 prompt、个人信息、密钥或完整模型输出。

## 3. 总 deadline：重试不能重置时钟

最常见的超时错误是为每次请求都设置 `timeout=8`，然后主模型两次、fallback 两次。用户看到的最坏延迟会超过 32 秒，还不包括退避、排队和连接建立。

在入口计算一次单调时钟 deadline：

```text
deadline = monotonic_now + business_budget
remaining = deadline - monotonic_now
attempt_timeout = min(per_try_cap, remaining - reserve_for_fallback)
```

实时请求可以为 fallback 预留时间。例如 8 秒总预算中，主 Gemini 首次最多 3.0 秒，一次重试最多 1.5 秒，退避不超过 0.25 秒，至少保留约 3 秒给 fallback 和响应编码。离线任务不必照搬这些数字，应依据真实 P95/P99、队列 SLA 和输出长度压测。

规则：

1. 使用 `time.monotonic()`，不要用可能被校时影响的墙上时钟计算剩余时间。
2. DNS、连接、读取、退避、模型切换和输出验证都消耗同一个总预算。
3. 剩余时间不足以完成一次有意义的调用时立即停止，返回明确的 `deadline_exhausted`。
4. 将 deadline 继续传给下游工具；下游不能重新获得完整预算。
5. 流式响应要区分“首 token deadline”和“完整响应 deadline”，流开始不代表业务已成功。

## 4. 有限重试：按错误、幂等性和预算决策

建议默认由业务执行层重试并设置 SDK `max_retries=0`，避免双层重试。一个合理起点是每个候选最多 2 次尝试、整条链路最多 3 次模型调用，而不是每层各重试 3 次。

| 结果 | 是否立即重试 | 是否可 fallback | 说明 |
| --- | --- | --- | --- |
| 连接失败、读取超时 | 有剩余预算时至多一次 | 是 | 先确认请求是否有副作用 |
| 408、429、500、502、503、504 | 有限、带 jitter | 是 | 尊重 `Retry-After`，但不得越过 deadline |
| 401、403 | 否 | 通常否 | key、权限或路由配置问题，切模型会掩盖错误 |
| 400、422 | 否 | 仅当已知模型兼容差异 | 先修请求或 schema |
| 内容安全拒绝 | 否 | 不应为绕过策略而切换 | 按业务合规流程处理 |
| 余额/配额配置错误 | 否 | 按预设独立账户策略 | 告警，不做无限探测 |
| 输出不符合 schema | 通常不重试同一输出 | 可切已验证候选 | 记录 `validation_error` |

退避使用 full jitter，例如在 `[0, min(cap, base * 2^attempt)]` 中随机取值。并发限流比长时间重试更重要：没有每租户和全局并发上限时，重试会占满连接池，使健康候选也无法服务。

## 5. 熔断器：保护系统，不是替代告警

建议按“实际路由目标”维护熔断器，例如 `model + cost_group + region`，而不是为所有模型共用一个开关。

- **CLOSED**：正常放行；只把连接错误、超时、429 和选定 5xx 计作熔断失败。
- **OPEN**：达到阈值后，在冷却期内快速失败并跳过候选，把时间留给 fallback。
- **HALF_OPEN**：冷却结束后只允许少量探测；成功关闭，失败重新打开。

单进程内存状态只保护当前 worker。多实例生产系统可使用 Redis/网关控制面共享窗口，但要考虑 Redis 故障时的默认策略；通常“本地短熔断 + 集中指标告警”比让所有请求依赖一个脆弱的全局锁更安全。阈值必须同时考虑最小样本量和失败比例，低流量服务仅凭连续两次失败就长期开路容易误判。

熔断打开仍要发出 `circuit_open` 事件。不要将跳过请求算作供应商调用失败，也不要让 fallback 成功把主路故障从告警中消失。

## 6. Python：deadline、有限重试、熔断、fallback 与 JSON 日志

下面是可直接改造的同步骨架。模型名与成本分组需映射为账户实际可用配置；header 也应以实际接入约定为准。代码关闭 SDK 自动重试，确保唯一的尝试预算由路由器掌控。

```python
from __future__ import annotations

import json
import os
import random
import time
import uuid
from dataclasses import dataclass
from threading import Lock
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
)

client = OpenAI(
    api_key=os.environ["VIRALAPI_API_KEY"],
    base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
    max_retries=0,  # one retry owner: this router
)

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Candidate:
    model: str
    cost_group: str
    max_attempts: int
    per_try_cap_s: float


ROUTES = {
    "support": [
        Candidate("gemini-primary", "stable_official", 2, 3.0),
        Candidate("gpt-fallback", "stable_official", 1, 2.5),
    ],
    "batch_content": [
        Candidate("gemini-flash", "welfare", 2, 15.0),
        Candidate("claude-fallback", "official_transfer", 1, 15.0),
    ],
}


class CircuitOpen(RuntimeError):
    pass


class CircuitBreaker:
    """Small single-process example; use shared state deliberately if required."""

    def __init__(self, threshold: int = 4, cooldown_s: float = 30.0):
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self.failures = 0
        self.opened_at: float | None = None
        self.half_open_probe = False
        self.lock = Lock()

    def allow(self, now: float) -> str:
        with self.lock:
            if self.opened_at is None:
                return "closed"
            if now - self.opened_at < self.cooldown_s:
                raise CircuitOpen("circuit cooling down")
            if self.half_open_probe:
                raise CircuitOpen("half-open probe already in flight")
            self.half_open_probe = True
            return "half_open"

    def success(self) -> None:
        with self.lock:
            self.failures = 0
            self.opened_at = None
            self.half_open_probe = False

    def failure(self, now: float) -> None:
        with self.lock:
            self.failures += 1
            if self.half_open_probe or self.failures >= self.threshold:
                self.opened_at = now
            self.half_open_probe = False


BREAKERS: dict[tuple[str, str], CircuitBreaker] = {}


def emit(event: str, **fields: Any) -> None:
    # Configure the log sink to redact prompts, outputs, PII, and credentials.
    print(json.dumps({"ts": time.time(), "event": event, **fields}, ensure_ascii=False))


def classify(exc: Exception) -> tuple[bool, int | None]:
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return True, None
    if isinstance(exc, APIStatusError):
        return exc.status_code in RETRYABLE_STATUS, exc.status_code
    return False, None


def valid_output(text: str | None) -> bool:
    # Replace with JSON Schema, safety, and domain-specific validation.
    return bool(text and text.strip())


def complete(
    messages: list[dict[str, str]],
    *,
    tenant_id: str,
    scenario: str,
    total_deadline_s: float,
) -> str:
    request_id = str(uuid.uuid4())
    started = time.monotonic()
    deadline = started + total_deadline_s
    total_attempts = 0
    last_error: Exception | None = None

    for fallback_index, candidate in enumerate(ROUTES[scenario]):
        breaker = BREAKERS.setdefault(
            (candidate.model, candidate.cost_group), CircuitBreaker()
        )
        try:
            circuit_state = breaker.allow(time.monotonic())
        except CircuitOpen as exc:
            emit(
                "circuit_skip", request_id=request_id, tenant_id=tenant_id,
                scenario=scenario, model=candidate.model,
                cost_group=candidate.cost_group, fallback_index=fallback_index,
                error_type=type(exc).__name__, degraded=True,
            )
            last_error = exc
            continue

        for attempt in range(1, candidate.max_attempts + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0.20:  # retain time to serialize a controlled response
                emit("deadline_exhausted", request_id=request_id,
                     tenant_id=tenant_id, scenario=scenario,
                     elapsed_ms=round((time.monotonic() - started) * 1000))
                raise TimeoutError("total LLM deadline exhausted") from last_error

            timeout_s = min(candidate.per_try_cap_s, remaining - 0.10)
            attempt_started = time.monotonic()
            total_attempts += 1
            try:
                response = client.with_options(timeout=timeout_s).chat.completions.create(
                    model=candidate.model,
                    messages=messages,
                    temperature=0.2,
                    extra_headers={
                        "X-Request-ID": request_id,
                        "X-Tenant-ID": tenant_id,
                        "X-Scenario": scenario,
                        "X-Cost-Group": candidate.cost_group,
                    },
                )
                text = response.choices[0].message.content
                if not valid_output(text):
                    raise ValueError("output_validation_failed")
                breaker.success()
                usage = getattr(response, "usage", None)
                emit(
                    "llm_success", request_id=request_id, tenant_id=tenant_id,
                    scenario=scenario, model=candidate.model,
                    cost_group=candidate.cost_group, attempt=attempt,
                    total_attempts=total_attempts, fallback_index=fallback_index,
                    degraded=fallback_index > 0,
                    circuit_state=circuit_state,
                    latency_ms=round((time.monotonic() - attempt_started) * 1000),
                    deadline_remaining_ms=max(0, round((deadline - time.monotonic()) * 1000)),
                    prompt_tokens=getattr(usage, "prompt_tokens", None),
                    completion_tokens=getattr(usage, "completion_tokens", None),
                )
                return text
            except Exception as exc:
                retryable, status_code = classify(exc)
                # Invalid output may fallback, but does not indicate transport health.
                validation_error = isinstance(exc, ValueError)
                if retryable:
                    breaker.failure(time.monotonic())
                last_error = exc
                emit(
                    "llm_attempt_failed", request_id=request_id,
                    tenant_id=tenant_id, scenario=scenario,
                    model=candidate.model, cost_group=candidate.cost_group,
                    attempt=attempt, total_attempts=total_attempts,
                    fallback_index=fallback_index, degraded=True,
                    retryable=retryable, validation_error=validation_error,
                    status_code=status_code, error_type=type(exc).__name__,
                    latency_ms=round((time.monotonic() - attempt_started) * 1000),
                    deadline_remaining_ms=max(0, round((deadline - time.monotonic()) * 1000)),
                )
                if validation_error or not retryable:
                    break
                if attempt == candidate.max_attempts:
                    break
                sleep_s = random.uniform(0, min(0.5, 0.1 * (2 ** (attempt - 1))))
                if time.monotonic() + sleep_s + 0.20 >= deadline:
                    break
                time.sleep(sleep_s)

    raise RuntimeError(
        f"all model routes exhausted; request_id={request_id}; "
        f"last_error={type(last_error).__name__ if last_error else 'none'}"
    ) from last_error
```

生产化时还应增加：异步客户端连接池、租户 semaphore、队列幂等键、`Retry-After` 上限解析、共享或分层熔断、OpenTelemetry trace、token/费用账本，以及 schema 校验失败后的专门错误类型。异常消息可能包含敏感响应，示例日志只记录异常类别，不输出 key、prompt 或原始正文。

## 7. fallback 是受控降级，不是质量免责

跨模型 fallback 前应建立兼容性矩阵：

- 系统提示、最大上下文和输出 token 上限是否等价；
- JSON/工具调用的参数和结束原因能否被同一解析器识别；
- 内容安全策略是否会改变业务风险；
- 多语言、引用、日期、金额等关键样本是否通过回归测试；
- fallback 是否允许执行写操作，还是只能给只读答案；
- 是否需要在 UI 标记“降级结果”或要求人工确认。

fallback 成功时，用户级结果可以是 success，但运维事件必须是 `degraded=true`。分别统计：主路成功率、最终成功率、降级率、每个成功请求的平均调用次数、breaker open 时长、P95/P99、验证失败率以及单位成功结果成本。

## 8. 结构化日志与成本归因

每次**尝试**至少记录：

- 身份与业务：`request_id`、`tenant_id`、`scenario`、`feature`；
- 路由：`model`、`cost_group`、`fallback_index`、`attempt`、`circuit_state`；
- 结果：`status_code`、`error_type`、`retryable`、`validation_error`、`degraded`；
- 时间：`latency_ms`、`deadline_remaining_ms`、队列等待时间；
- 用量：输入/输出 token、缓存 token（若有）、估算费用与币种；
- 关联：trace/span ID、部署版本和策略版本。

基于这些字段可以回答：“哪个租户因 Gemini 429 产生了额外 fallback 成本？”而不是只看到总账单上涨。日志用 request ID 关联正文存储，但正文应单独加密、限权并设置保留期；API key、Authorization header 和完整个人数据不得进入常规日志。

## 9. 按场景、预算、稳定性做成本路由

精确价格口径为：**福利分组约官方 1.5 折，官转分组约官方 6 折，稳定官方分组约官方 8 折**。选择必须强调**场景、预算、稳定性**，不能把价格分组当作质量保证，也不应默认所有请求走最低价。

- 客户可见的实时客服、付费 SaaS 核心能力：通常优先稳定官方分组，并准备一个经过压测的跨模型 fallback。
- 内部知识助手、常规摘要和有人工复核的工作流：可评估官转分组，平衡预算与稳定性。
- 可排队、可重放、可自动校验的内容批处理：可评估福利分组，同时设置并发、日预算和死信队列。
- 单次调用便宜但重试/降级频繁的路线，最终“每个有效结果成本”可能更高，应按成功结果核算，而非只看标称单价。

建议路由器在月预算达到 70%/90% 时分别告警和限制低优先级任务；核心任务不能在预算耗尽时悄悄切到未经验证的模型。

## 10. 适用与不适用边界

**适合：**有真实 API 调用量、具备基本接入与排障能力的开发者、小团队、自动化业务、SaaS 团队和同行渠道；尤其适合希望用统一 OpenAI-compatible 边界管理 Gemini、GPT、Claude，并需要显式控制稳定性与成本的团队。

**不适合：**只寻求免费/无限试用、缺少基本 API 技术能力且需要高强度代运维、低预算但高售后消耗、滥用或违规场景。对医疗诊断、资金交易、风控放行、安全关键控制等高后果决策，通用 LLM fallback 不能替代领域验证、确定性规则、审计和人工审批。要求严格同一模型可复现性、数据驻留或供应商直接 SLA 的系统，也应先确认合同与合规边界，不应仅依赖跨模型降级。

## 11. 上线检查表

1. 为每个 `tenant + scenario` 写明总 deadline、候选数、每候选尝试数和最终失败语义。
2. 关闭重复的 SDK 重试；压测最坏尝试次数不越过入口 deadline。
3. 只把明确瞬态错误计入 breaker；401/403/400 走配置告警。
4. 为实时链路保留 fallback 时间，为离线链路使用队列和幂等键。
5. 对每个 fallback 运行固定回归集、schema、安全和领域校验。
6. 设置租户/全局并发上限、日/月预算及熔断恢复探测上限。
7. 仪表盘同时显示主路成功率、最终成功率、降级率和单位有效结果成本。
8. 演练 Gemini 429、慢响应、5xx、无效 JSON、fallback 也失败和日志后端故障。
9. 确认 prompt、密钥、个人信息不会写入普通日志。
10. 准备人工接管、死信队列和关闭 fallback 的回滚开关。

## FAQ

### 1. Gemini 超时后应该先重试还是立刻 fallback？

取决于剩余 deadline 和错误分布。短暂连接抖动可以在主模型上带 jitter 重试一次；实时链路若剩余时间不足，应立即切到经过验证的候选。不要让重试耗尽 fallback 的全部预算。

### 2. 为什么总 deadline 比单次 timeout 更重要？

单次 timeout 只限制一次网络调用，无法限制退避、多模型切换和验证的总时间。总 deadline 才对应用户或队列任务真正承诺的 SLA。

### 3. 熔断器应该按 Gemini 模型还是按整个供应商设置？

优先按实际故障隔离单元设置，如 `model + cost_group + region`。再用上层供应商指标做告警；过大的熔断范围会误伤健康路线，过细则不能阻止故障流量放大。

### 4. fallback 返回 HTTP 200 是否就算成功？

不是。必须通过 JSON Schema、安全、权限和领域不变量验证。用户级可算成功，运维上仍应标记 `degraded=true` 并计入降级率与额外成本。

### 5. 三种分组怎么选？

福利分组约官方 1.5 折，官转分组约官方 6 折，稳定官方分组约官方 8 折。应依据场景、预算、稳定性和压测结果选择：可重跑批处理更能承受低成本路线，客户可见核心链路通常更看重稳定性。价格不是 SLA 承诺。

### 6. 在哪里查看资料并联系 ViralAPI？

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- Email：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866
