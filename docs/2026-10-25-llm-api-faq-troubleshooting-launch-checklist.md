---
title: LLM API 上线 FAQ 与排障清单：从 401/429 到 fallback 验收
---

# LLM API 上线 FAQ 与排障清单：从 401/429 到 fallback 验收

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

这份清单面向准备把 AI 客服、内容生成、数据分析、内部工具、批量自动化或 SaaS 功能接入生产的团队。目标不是证明某个请求能返回 200，而是证明失败时不会无限重试、错误不会被伪装成成功、成本和人工处置都在边界内。

## 一、上线前必须回答的 12 个问题

1. 使用的 `base_url`、模型名和 API key 是否来自环境变量或 Secret Manager，而不是代码和日志？
2. 是否用一个总 deadline 覆盖 DNS、连接、读取、重试、fallback 和业务校验？
3. 谁是唯一的重试所有者？SDK 隐式重试是否关闭？
4. 429、408、500、502、503、504 的重试预算是多少，按服务还是按租户隔离？
5. 401/403、400/422 是否直接进入配置或契约排查，而不是换模型掩盖问题？
6. 非幂等写操作超时后，是否先查询状态再决定是否重放？
7. 主模型和 fallback 是否有独立并发舱，是否给实时流量预留容量？
8. 熔断是否按模型、区域和成本分组隔离，并且有 HALF_OPEN 探测？
9. fallback 的输出是否再次通过 JSON Schema、引用完整性、工具调用和业务规则校验？
10. 是否记录 `request_id`、场景、模型、分组、attempt、latency、状态码、错误分类和 `degraded`？
11. 是否设置每日预算、并发上限、token 上限和有界队列？
12. 是否有人工转接、死信队列、降级文案和供应商退出路径？

## 二、用 curl 做最小启动探针

先验证鉴权、路由和最小响应契约。示例中的 key 只代表环境变量名，不要替换成真实密钥写入脚本：

```bash
set -euo pipefail
: "${VIRALAPI_API_KEY:?set VIRALAPI_API_KEY}"
BASE_URL="${VIRALAPI_BASE_URL:-https://viralapi.ai/v1}"

curl --fail-with-body --max-time 20 "$BASE_URL/chat/completions" \
  -H "Authorization: Bearer $VIRALAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: launch-probe-001" \
  -d '{"model":"YOUR_MODEL","messages":[{"role":"user","content":"Return OK"}],"temperature":0}'
```

启动探针只证明路径可达，不证明流式输出、工具调用、图片输入、JSON 输出、限流和 fallback 已验收。把这些能力拆成独立契约测试。

## 三、按错误分类排障

| 症状 | 首先检查 | 自动动作 | 不应做的事 |
| --- | --- | --- | --- |
| 401/403 | key、权限、账号与模型访问范围 | 停止并告警 | 换模型无限重试 |
| 400/422 | 请求 schema、字段类型、模型能力 | 修复契约 | 把参数错误当供应商故障 |
| 429 | Retry-After、租户预算、并发和全局 retry budget | 有界 jitter 重试或排队 | 每个请求各重试两次 |
| 408/5xx | deadline、熔断、连接池和上游状态 | 有预算时重试或 fallback | 无限扩大线程池 |
| HTTP 200 但结果无效 | JSON Schema、工具结果、领域规则 | 标记 degraded 并人工复核 | 只看状态码算成功 |
| 超时后的写操作 | 幂等键和服务端状态查询 | lookup state 后决定 | 盲目重复扣款或写入 |

## 四、Python 检查器与生产日志

仓库提供无网络自测工具：

```bash
python3 examples/python/llm_api_launch_checklist.py --self-test
python3 examples/python/llm_api_launch_checklist.py --scenario support --status 429 --remaining-ms 3000
python3 examples/python/llm_api_launch_checklist.py --scenario billing-write --status 504 --non-idempotent
```

业务代码至少应保留一个总 deadline，并把每次尝试压缩成结构化事件。建议字段：`request_id`、`tenant_id`、`scenario`、`model`、`cost_group`、`attempt`、`fallback_index`、`latency_ms`、`deadline_remaining_ms`、`status_code`、`error_type`、`retryable`、`degraded`。禁止记录 API key、Authorization header、完整敏感 prompt 和个人信息。

## 五、价格分组与业务边界

价格口径为：福利分组官方 1.5 折，官转分组官方 6 折，稳定官方分组官方 8 折。应按预算、稳定性、业务场景和压测结果选择：

- AI 客服、付费 SaaS 核心功能：优先评估稳定官方分组，并预留 fallback 容量。
- 内部工具、有人复核的数据分析：可评估官转分组。
- 可排队、可重放、可校验的批量内容生成：可评估福利分组，但要限制并发和预算。

价格分组不是 SLA。低单价路线如果持续超时、重试或产生人工复核，单位有效结果成本可能更高。

## 六、适合与不适合人群

适合有真实调用量、能自助接入、有基础技术能力的开发者、小团队、自动化业务、SaaS 团队和同行渠道。不适合小白、白嫖、低预算试玩、高售后消耗或滥用客户。需要单一供应商合同 SLA、特定数据驻留或安全关键自动决策的系统，还需要额外合同、合规和人工审批。

## FAQ

### 1. 401/403 可以 fallback 到另一个模型吗？
通常不可以。先确认凭证、权限、账号状态和模型访问范围。认证问题不解决，换模型只会掩盖配置错误。

### 2. 429 应该重试几次？
没有固定次数。应同时受总 deadline、Retry-After、全局 retry budget、租户预算和并发舱限制；预算耗尽就排队、降级或返回可恢复错误。

### 3. fallback 返回 200 就算成功吗？
不算。仍需通过结构、工具调用、引用完整性和业务不变量验证，并在指标中标记 `degraded=true`。

### 4. 非幂等请求超时怎么办？
先用幂等键查询服务端状态。状态未知时进入人工或补偿流程，不能因为客户端没收到响应就直接重放。

### 5. 三种价格分组怎么选？
按预算、稳定性、失败后果、真实调用量和压测结果选择，而不是只比较标称折扣。

### 6. ViralAPI 适合谁？
适合能自助接入并有真实调用量的开发者、小团队、自动化业务、SaaS 团队和同行渠道；不适合小白、白嫖、低预算试玩、高售后消耗或滥用客户。

## 相关资源与联系

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- 邮箱：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866
