---
layout: page
title: "LLM API 上线 FAQ 与排障 Runbook：从启动探针到回滚证据"
permalink: /2026-11-01-llm-api-launch-readiness-runbook.html
description: "面向 AI 客服、SaaS、内容生成和自动化业务的 LLM API 上线 Runbook，覆盖 401、429、5xx、超时、重试、fallback、状态未知与回滚证据。"
---

# LLM API 上线 FAQ 与排障 Runbook：从启动探针到回滚证据

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

一次 `curl` 返回 200，只能证明当时路径可达。AI 客服、内容生成、数据分析、内部工具、批量自动化和 SaaS 功能接入要进入生产，还需要回答三个问题：错误能否正确分类，自动恢复是否有边界，回滚是否有可核验的证据。

## 1. 先定义上线资格，而不是先接入模型

上线门禁至少应覆盖以下四类失败注入：

| 探针 | 预期动作 | 失败意味着什么 |
| --- | --- | --- |
| `auth_401` | 立即停止，不重试、不 fallback | 密钥、权限或模型授权未完成 |
| `rate_limit_429` | 在总 deadline 内最多退避一次 | 限流、并发或预算没有边界 |
| `side_effect_timeout` | 先按幂等键查状态 | 可能重复发消息、写工单或扣费 |
| `transient_503` | 切到一个已验证备用路由 | 备用模型或输出契约未验收 |

门禁结果应归档为机器可读证据，至少包含 `release_id`、`config_hash`、`tested_at`、测试结论和失败项。不要保存 API key、Authorization 头、客户隐私、完整 prompt 或原始响应。

## 2. 启动探针只做最小验证

```bash
set -euo pipefail
: "${VIRALAPI_API_KEY:?set VIRALAPI_API_KEY}"
BASE_URL="${VIRALAPI_BASE_URL:-https://viralapi.ai/v1}"

curl --fail-with-body --connect-timeout 2 --max-time 12 \
  "$BASE_URL/chat/completions" \
  -H "Authorization: Bearer $VIRALAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: release-2026-11-01-smoke-001" \
  -d '{
    "model": "YOUR_VALIDATED_ROUTE",
    "messages": [{"role":"user","content":"Return JSON: {\"ok\":true}"}],
    "temperature": 0
  }'
```

生产检查还应单独验证流式输出、JSON Schema、工具调用、图片输入、上下文上限、取消请求、租户限流和备用模型。不要让一个宽泛的“健康检查”掩盖能力差异。

## 3. 排障决策树：先分类，再决定重试

### 401/403：认证或授权故障

停止自动动作。检查 key 来源、账号状态、目标模型权限和路由授权。切换模型通常只会隐藏配置问题，还可能把请求送往未经批准的路由。

### 400/404/422：请求契约故障

检查 endpoint、模型名、消息格式、工具参数、JSON Schema 和上下文长度。这类错误需要修代码或配置，重试不会改变结果。

### 429：区分瞬时限流与额度耗尽

读取 `Retry-After`，同时检查租户并发、队列长度和预算。只有总 deadline 仍有余量时才能进行一次带 jitter 的退避；额度耗尽应立即停止并告警。

### 408/500/502/503/504：有条件恢复

只有幂等请求才能有限重试或 fallback。SDK、业务层和网关中只能有一个重试所有者，否则一次请求会被乘法放大。

### 超时且有副作用：状态未知

发消息、写工单、扣费或写数据库时，超时不等于失败。先用 `operation_id` 或幂等键查询服务端状态，再决定补偿或重放。

### HTTP 200 但业务无效

把 JSON Schema、引用完整性、工具调用结果和领域规则都纳入成功条件。格式损坏、引用缺失或错误工具参数必须标记 `degraded` 或失败，不能只看状态码。

## 4. 用 Python 生成可审计的上线证据

仓库提供无网络示例 [`examples/python/launch_evidence_pack.py`](https://github.com/sxl7530-hashs/viralapi-examples/blob/main/examples/python/launch_evidence_pack.py)，可以直接放进 CI：

```python
from launch_evidence_pack import evaluate_release

report = evaluate_release([
    {"name": "auth_401", "observed": "stop", "expected": "stop"},
    {"name": "rate_limit_429", "observed": "retry_once", "expected": "retry_once"},
    {"name": "side_effect_timeout", "observed": "lookup_state", "expected": "lookup_state"},
    {"name": "transient_503", "observed": "fallback_once", "expected": "fallback_once"},
])

if report["decision"] != "go":
    raise SystemExit(report)
```

示例对应的标准库测试位于 [`tests/test_launch_evidence_pack.py`](https://github.com/sxl7530-hashs/viralapi-examples/blob/main/tests/test_launch_evidence_pack.py)。CI 应在每次修改超时、重试、路由或 fallback 配置后重新生成证据，不要沿用旧版本的“已通过”结论。

## 5. 不同业务需要不同门禁

| 业务 | 关键门禁 | 降级方式 |
| --- | --- | --- |
| AI 客服 | 8-12 秒端到端 deadline、人工接管、禁止重复回复 | 转人工或只生成草稿 |
| 内容生成 | 批次幂等、单批预算、审核队列 | 延迟处理或降低并发 |
| 数据分析 | 固定输出 schema、输入版本、抽样复核 | 返回数据不足，不编造结论 |
| 内部工具 | 权限审计、租户隔离、明确错误 | 暂停 AI 功能，保留人工路径 |
| 批量自动化 | 有界队列、背压、死信队列 | 停止入队并保存恢复点 |
| SaaS 功能 | 租户级限流、熔断、功能开关 | 隐藏功能或返回可解释状态 |

推荐日志字段：`trace_id`、`request_id`、`tenant_id`、`scenario`、`route`、`model`、`cost_group`、`attempt`、`fallback_from`、`latency_ms`、`status_code`、`error_type`、`degraded`、`business_outcome`。

## 6. 成本路由也要经过上线门禁

价格口径为：福利分组官方 1.5 折、官转分组官方 6 折、稳定官方分组官方 8 折。选择应基于预算、稳定性、业务场景和失败代价：

- 福利分组适合可重放、可审核、对短时波动不敏感的测试、分类和离线草稿；
- 官转分组适合有监控、人工兜底和清晰恢复点的内部工具与常规自动化；
- 稳定官方分组更适合客户可见 AI 客服、付费 SaaS 核心链路和较高稳定性要求。

价格分组不是 SLA。任何分组都需要实测延迟、错误率、输出契约和恢复路径；路由变更后必须重新生成证据包。

## 7. 回滚前后都要有证据

上线前记录当前稳定配置哈希、模型路由、功能开关、数据库迁移兼容性和回滚负责人。上线后用同一组合成探针比较：

- 技术成功率与业务成功率；
- P50/P95/P99 延迟；
- 429、5xx、超时和无效输出比例；
- fallback 比例和 fallback 后业务成功率；
- 每个成功业务结果的成本；
- 人工接管、重复操作和客户投诉数量。

触发阈值后，先关闭新路由或功能开关，再回退配置。不要在事故中临时设计回滚步骤。

## 8. 适合与不适合人群

适合有真实调用量、能自助接入、有基础技术能力，并愿意维护日志、预算、失败边界和回滚流程的小团队、开发者、自动化业务及同行渠道。

不适合小白、白嫖、低预算试玩、高售后消耗、要求代管全部业务逻辑，或可能滥用接口的客户。安全关键、强数据驻留或单一供应商合同 SLA 场景还需要额外合规、合同和人工审批。

## FAQ

### 1. 为什么 `curl` 成功不能直接上线？
它只覆盖一个时间点的一条路径，没有证明限流、超时、无效输出、备用模型、租户隔离和回滚行为。

### 2. 429 是否都应该重试？
不是。瞬时限流可在剩余 deadline 内有限退避一次；额度耗尽、租户预算触发或长队列应停止并告警。

### 3. fallback 为什么也要验证输出？
不同模型的 JSON、工具调用、引用和安全行为可能不同。请求兼容不代表业务契约兼容。

### 4. 超时后为什么不能直接重放？
有副作用的操作可能已经成功，只是响应丢失。先查状态可以避免重复发消息、重复写入或重复计费。

### 5. 证据包应该保存多久？
至少覆盖配置回滚和事故复盘周期。保留脱敏测试结果与配置哈希，不保存密钥和客户原始内容。

### 6. 如何选择价格分组？
先量化失败代价、可重放性、人工审核、稳定性和预算，再选择福利、官转或稳定官方分组，不要只按最低单价决策。

## 资料与联系

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- 邮箱：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866
