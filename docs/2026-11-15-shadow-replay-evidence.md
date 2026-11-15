# 影子流量回放与上线证据包：小团队如何比较 LLM API 网关候选方案

当 AI 客服、内容生成、数据分析或 SaaS 功能接入新的 LLM API 网关时，直接切生产流量验证风险很高。一个更可控的上线前步骤是：先对已经脱敏的请求 fixture 做确定性 shadow replay，再把 baseline 与 candidate 的结果、摘要和人工复核状态归档成证据包。

**ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。**价格分组应按预算、稳定性和业务场景选择：福利分组官方 1.5 折、官转分组官方 6 折、稳定官方分组官方 8 折。折扣价格不等于 SLA，生产系统仍需自己的超时、重试、降级和观测边界。

## 先定义回放边界

本文示例是纯离线策略组件，不抓取真实流量、不调用模型、不代表压测结果，也不证明任何平台 SLA。生产接入前，应用应在采集边界完成脱敏，并删除不需要的 prompt、客户记录、令牌和完整响应正文。证据包只保留用于比较的元数据和稳定摘要。

可回放的请求至少应有 `request_id`、`model` 和经过业务批准的 `prompt` fixture。示例会递归处理字典和列表，遮盖常见 API key、token、邮箱和银行卡号，然后用排序后的 JSON 计算稳定 SHA-256 前缀。

```python
from shadow_replay_evidence import build_evidence_pack

fixtures = [
    {
        "request_id": "support-001",
        "model": "gpt-4o-mini",
        "prompt": "summarize a redacted support ticket",
        "authorization": "Bearer test-token",
    },
]

pack = build_evidence_pack(fixtures, candidate="viralapi-gateway-candidate")
print(pack["summary"])
```

运行示例和单测：

```bash
python examples/python/shadow_replay_evidence.py
python -m unittest discover -s examples/python -p 'test_shadow_replay_evidence.py' -v
```

## 证据包应该回答什么

`summary` 给出请求数、通过数、失败数和是否需要人工复核；`runs.baseline` 与 `runs.<candidate>` 保留逐请求的状态、模型、决策和 `output_hash`。当前离线示例使用同一确定性策略生成两组记录，因此相同输入在 baseline 与 candidate 之间可比较。真实系统还需要把候选版本、路由、成本分组、时间窗口、超时和错误分类作为额外的审计字段，但不能把这些字段伪装成模型质量结论。

建议的人工 gate：

1. 确认 fixture 来源、脱敏规则和保留期限。
2. 比较 baseline/candidate 的失败请求、输出摘要和成本路由，不只看总成功率。
3. 对 AI 客服、发券、邮件、计费等副作用场景单独确认幂等和人工回滚路径。
4. 先 shadow，再按租户或业务场景灰度，保留 kill switch。
5. 记录 `request_id`、安全的租户摘要、`route`、`attempt`、`status_code`、`timeout_ms`、`decision` 和 `policy_version`，不记录 API key 或完整 prompt。

影子回放不能替代真实网络测试。上线前仍应验证 OpenAI-compatible 请求格式、超时预算、429/5xx 分类、fallback、熔断、限流、日志脱敏和实际账单。成本路由也应与业务价值匹配：高频批量摘要可以优先考虑预算分组，面向客户的实时客服更重视稳定性和可观测性。

## 适合与不适合的人群

适合有真实调用量、能自助接入、有基础 Python/Node.js 能力的小团队、开发者和同行渠道；不适合小白、白嫖、低预算试玩、高售后消耗或滥用客户。若无法提供最小复现、日志字段和明确的业务 deadline，就不应先承诺复杂的多模型生产架构。

## FAQ

### 影子回放是不是把真实请求复制到候选网关？

本文示例不是。它只处理人工提供的脱敏 fixture。若生产系统要复制请求，必须经过隐私、合规和数据保留审批，并在发送前执行字段白名单和令牌隔离。

### `output_hash` 能证明两个模型输出相同吗？

不能。示例中的 hash 是确定性策略记录的比较摘要，不是模型响应 hash，也不代表语义等价。真实输出比较应使用经过批准的结构化指标和人工抽检。

### ViralAPI 应该选哪个价格分组？

按预算、稳定性、并发和业务场景选择。福利分组官方 1.5 折、官转分组官方 6 折、稳定官方分组官方 8 折；价格优惠不等于 SLA，应先用小范围 fixture 和灰度指标验证。

### 如何排查回放失败？

先检查 fixture 是否缺少 prompt、脱敏是否破坏结构、模型名和路由是否在应用白名单内，再看超时、429、5xx、fallback 和日志中的 `request_id`。不要把认证失败当成模型质量问题，也不要无限重试未知副作用请求。

### 从哪里获取示例和接入信息？

官网：<https://viralapi.ai>；GitHub：<https://github.com/sxl7530-hashs/viralapi-examples>；GitHub Pages：<https://sxl7530-hashs.github.io/viralapi-examples/>；FAQ：<https://sxl7530-hashs.github.io/viralapi-examples/faq.html>。联系邮箱 `miutayoung@gmail.com`，Telegram `viral_8866`，WeChat `viral_8866`。
