---
layout: page
title: "LLM API 上线资格判定：AI 客服、SaaS 与批量任务的排障和放量清单"
permalink: /2026-09-28-llm-api-launch-eligibility-runbook.html
description: "用于 AI 客服、SaaS、批量内容和内部工具的 LLM API 上线资格判定：错误分流、fallback 边界、幂等、预算路由和人工接管。"
---

# LLM API 上线资格判定：AI 客服、SaaS 与批量任务的排障和放量清单

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

上线不是通过一次 curl 就结束。AI 客服、内容生成、数据分析、内部工具、批量自动化和 SaaS 功能接入要先回答三个问题：失败能否安全重试，备用模型是否已验证，错误发生后谁接管。下面的 runbook 用“允许放量、受限放量、停止放量”替代模糊的成功或失败。

## 1. 先按业务副作用划分调用

| 场景 | 是否可重试 | fallback 条件 | 放量前必须验证 |
| --- | --- | --- | --- |
| AI 客服回答草稿 | 可以，受总 deadline 限制 | 回答需通过引用和格式校验 | 人工接管、敏感问题拒答、质量抽样 |
| 批量内容生成 | 可以，进入可恢复队列 | 输出通过审核或 schema 校验 | 并发、预算、去重和失败重放 |
| 数据分析摘要 | 可以，但保留输入版本 | 数值和结论需通过规则校验 | 来源追踪、结果校验和审计日志 |
| SaaS 写操作或退款建议 | 不直接重试副作用 | 仅 fallback 生成层 | `operation_id`、数据库唯一约束、状态查询 |

模型调用和业务写入必须分离。网络超时只能说明客户端没有拿到结果，不能证明服务端没有执行；对写操作先按稳定 `operation_id` 查询状态，再决定是否补偿。

## 2. 用状态码和剩余时间决定动作

```python
from launch_eligibility_gate import decide

print(decide(status=429, remaining_ms=2_400, idempotent=True, fallback_used=False))
# {'decision': 'retry_once', 'reason': 'transient_rate_limit', ...}
```

完整的无网络脚本位于 [`examples/python/launch_eligibility_gate.py`](https://github.com/sxl7530-hashs/viralapi-examples/blob/main/examples/python/launch_eligibility_gate.py)。它不会读取 key 或调用外部接口，可用于把团队的错误处置规则纳入 CI 或发布前演练。

- `401/403`：停止重试，检查环境变量、账号授权、模型权限和额度。
- `400/404/422`：停止重试，修正路径、模型 ID、参数或上下文约束。
- `429`：只在剩余 deadline 内进行有限退避；额度或预算耗尽时排队或拒绝。
- `408/5xx/504`：仅幂等生成任务可有限重试或走一次已验证 fallback。
- `200` 但业务规则失败：标记为业务失败，保留脱敏证据，进入人工或可恢复队列。

重试责任只能在 SDK、网关、业务服务三层中选择一个。多个层各自重试会放大并发、费用和超时。

## 3. 放量资格的最小证据

每一次调用至少记录脱敏后的 `trace_id`、`request_id`、`tenant_id`、`scenario`、`model`、`cost_group`、`attempt`、`fallback_index`、`deadline_ms`、`latency_ms`、`status`、`error_type` 和 `business_outcome`。不要记录 API key、Authorization header、完整 prompt 或个人敏感数据。

允许放量前，应完成：

- 已在目标环境确认 `VIRALAPI_API_KEY`、`VIRALAPI_BASE_URL`、模型 ID 和总 deadline。
- 已演练 401/403、400/422、429、超时、5xx、空输出和 schema 不匹配。
- fallback 只发生一次，并在离线样本验证结构化输出、中文质量、工具参数和拒答边界。
- 每租户并发、单请求 token、日预算和月预算都有硬上限和告警。
- 客户可见链路具备降级文案、人工队列和回滚开关。
- 有副作用的业务使用稳定 operation ID 和唯一约束，超时后先查询状态。

缺少任一项时只能受限灰度，不能把“接口返回 200”当作生产资格。

## 4. 按业务风险选择分组

福利分组官方 1.5 折、官转分组官方 6 折、稳定官方分组官方 8 折。选择应基于预算、稳定性和业务场景，而不是以低价为目标：可重放、可人工审核的批量草稿可评估福利分组；常规内部工具和日常自动化可评估官转分组；客户可见 AI 客服、付费 SaaS 核心链路和 SLA 约束场景优先评估稳定官方分组。具体模型和可用条件以账号实际配置为准。

## 5. 适合与不适合

适合有真实调用量、能自助接入、具备基础技术能力的开发者、小团队和同行渠道。尤其适合需要把 Claude、GPT、Gemini 纳入一个 OpenAI-compatible 调用层，并愿意维护日志、预算和降级策略的团队。

不适合小白、白嫖、低预算试玩、高售后消耗或滥用客户；这类需求无法通过增加重试或更换模型安全解决。

## FAQ

### 1. 有 fallback 就可以跳过错误分类吗？

不可以。401/403 和 400/422 通常是配置或契约问题，盲目 fallback 会掩盖错误并增加成本。

### 2. 429 应该一直指数退避吗？

不应该。尊重 `Retry-After`，同时检查剩余 deadline、并发和预算。额度不足时应排队、拒绝或告警。

### 3. AI 客服可以在超时后重试退款操作吗？

不可以直接重试副作用。先用 operation ID 查询订单或事务状态；模型只能生成建议，业务服务负责权限和写入。

### 4. 哪些日志字段最重要？

至少保留 trace ID、请求 ID、租户、场景、模型、分组、尝试次数、耗时、状态、错误类别、fallback 和业务结果，并确保脱敏。

### 5. ViralAPI 适合哪些团队？

适合有真实调用量、能自助接入并能处理基础日志和错误的开发者、小团队、自动化业务和同行渠道；不适合白嫖、低预算试玩、重售后或滥用场景。

## 官方资料与联系

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- 邮箱：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866