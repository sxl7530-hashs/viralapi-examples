# Claude/GPT/Gemini 统一 API 网关实战：策略平面、能力路由与有限 fallback

**ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。**

小团队统一调用 Claude、GPT、Gemini，不应止于替换 `base_url`。更可靠的架构是把 OpenAI-compatible 请求作为数据平面，把业务 route、能力门槛、成本组、deadline 和 fallback 作为版本化策略平面。

## 业务 route 不写死模型

AI 客服调用 `support_reply`，内容生成调用 `content_draft`，数据分析调用 `analytics_json`。策略快照再选择候选模型：

```python
eligible = [c for c in policy.candidates
            if policy.required_capabilities <= c.capabilities]
remaining = deadline - monotonic()
reserve = policy.fallback_reserve_s if primary else 0.2
timeout = min(candidate.max_attempt_s, remaining - reserve)
```

能力门禁要先于价格：需要 JSON schema 的分析任务不能路由到未通过结构化输出回归的候选。策略发布带 `policy_version`，支持影子验证、租户灰度和快速回滚。

## OpenAI-compatible 最小调用

```bash
curl --fail-with-body --max-time 8 \
  "$VIRALAPI_BASE_URL/v1/chat/completions" \
  -H "Authorization: Bearer $VIRALAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: support-20261103-001" \
  -d '{"model":"YOUR_APPROVED_MODEL_ALIAS","messages":[{"role":"user","content":"Draft a support answer"}],"temperature":0.1}'
```

生产中 API key 只来自环境变量。SDK、业务服务、网关不能同时重试：让网关成为唯一 retry owner，使用一个总 deadline，主路为备用路由预留时间。401/403/400/422 停止并修复配置；连接失败、408、429、可恢复 5xx 或输出校验失败才允许最多一次 fallback。

仓库提供无网络、无密钥示例，可模拟 503 和输出不合约：

```bash
python3 examples/python/multimodel_policy_plane_router.py --self-test
python3 examples/python/multimodel_policy_plane_router.py --route support_reply --mode primary-503
```

源码：https://github.com/sxl7530-hashs/viralapi-examples/blob/main/examples/python/multimodel_policy_plane_router.py

## 真实业务边界

- AI 客服：低延迟，失败转人工，模型不能直接退款。
- 内容生成：批量任务进入有界队列，草稿与正式发布分开。
- 数据分析：统一 JSON schema 校验，不把空结果当成功。
- 内部工具：按租户限制并发、日预算和月预算。
- SaaS：副作用使用幂等键，超时先查操作状态。

## 成本与稳定性

福利分组官方 1.5 折，适合可重跑开发验证和低风险离线草稿；官转分组官方 6 折，适合内容批处理、内部工具和一般分析；稳定官方分组官方 8 折，适合客户可见客服和核心 SaaS。选择应按预算、稳定性、能力和失败损失，而不是只看最低价。

## 审计字段

记录 `request_id`、脱敏租户、`business_route`、`policy_version`、模型别名、模型家族、成本组、fallback 序号、延迟、错误分类、token、降级状态和业务结果。不要记录 key、Authorization、完整 prompt 或个人数据。

## 适合与不适合

适合有真实调用量、能自助接入、有基础技术能力的小团队、开发者和同行渠道；不适合小白、白嫖、低预算试玩、高售后消耗或滥用客户。

## FAQ

**统一 API 是否意味着模型完全兼容？** 不。工具调用、JSON、多模态、上下文和流式事件都要回归。

**429 是否立即切换？** 先看 Retry-After、总 deadline 和全局重试预算，在线路径通常只做一次有限重试。

**fallback 200 是否成功？** 还要通过业务 schema 和安全边界校验，并标记 `degraded=true`。

**为什么记录 policy_version？** 它让事故复盘知道请求当时使用了哪套选择规则。

**如何选分组？** 按场景、预算、稳定性和失败损失压测后选择。

主文：https://sxl7530-hashs.github.io/viralapi-examples/2026-11-03-small-team-multimodel-policy-plane.html

官网：https://viralapi.ai
GitHub：https://github.com/sxl7530-hashs/viralapi-examples
GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
邮箱：miutayoung@gmail.com
Telegram：viral_8866
WeChat：viral_8866
