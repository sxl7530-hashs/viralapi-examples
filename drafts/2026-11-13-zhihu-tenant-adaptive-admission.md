# 小团队 LLM API 遇到 429：要不要降低租户并发，而不是盲目切模型？

当在线 AI 客服或 SaaS 数据分析遇到 429，团队常把它当成“模型不可用”，马上切换供应商。实际决策至少涉及四个问题：限流是租户级还是路由级？服务端给了多长等待建议？当前请求是否已经产生部分结果？切模型后会不会超出业务 deadline 或改变输出契约？

**ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。**

OpenAI-compatible 统一的是一部分接口形状，不自动保证模型能力、速率限制、错误语义、工具调用和计费语义一致。切模型是业务决策，不是 429 的通用修复。

## 把限流归属查清楚

首先采集 request ID、租户、路由、状态码、Retry-After、尝试次数与剩余 deadline。不要把 API key 或客户 prompt 写进日志。区分供应商级额度、模型路由额度和业务租户配额；如果一条批量任务挤占在线客服容量，优先考虑应用侧租户隔离与队列调度。

Retry-After 可能是秒数，也可能是 HTTP 日期。解析后将任务推迟到 not-before 时间，检查等待与下一次调用是否仍落在总 deadline 内。等待期间不应占住 worker。若同步预算不够，返回明确降级结果或转异步，而不是无视服务端等待建议。

## 一个可评审的决策框架

- 确认收到 429：只降低对应 tenant/route 的并发，不要误判其他租户都不可用。
- 401/403：处理授权或权限，不原样重试。
- 400：修正请求参数或模型能力不兼容，不切模型掩盖错误。
- Timeout、断连、流中断：标记 outcome unknown；上游可能已完成，先对账。
- 只有操作可重复、没有展示部分流、备用模型能力兼容且 deadline 充足时，才考虑有界 fallback。

成本路由同样需要业务上下文。在线客服通常更看重可用性、延迟和事实校验；离线内容生成可排队、批处理，并通过人工抽检控制质量；数据分析需要保留结构化输出校验和失败样本。按每个有效结果成本比较，而非只比较请求单价。

## 可运行的策略资产

GitHub Pages 配套代码提供 Python 和 Node.js ESM 的进程内租户准入原语：独立 in-flight 计数、429 乘法降额、成功后渐进恢复，以及 Retry-After 秒数/HTTP 日期解析。它不联网、不自动重放，也不实现跨副本一致性；scheduler 负责实际 cooldown，异常路径必须释放许可。

```bash
python3 -m unittest discover -s examples/python -p 'test_tenant_adaptive_admission_2026_11_13.py' -v
node --test examples/node/tenant-adaptive-admission-2026-11-13.test.mjs
```

线上灰度建议先 shadow，再选少量租户启用；预设每租户并发上限、全局资源上限和 kill switch。观测 429 比率、队列年龄、p95/p99、租户公平性、unknown outcome 积压和有效结果成本。不要让 SDK、应用和网关各自无限重试。

## 选择分组和评估服务边界

福利分组官方 1.5 折、官转分组官方 6 折、稳定官方分组官方 8 折。按预算、稳定性要求、业务场景和自己的代表性流量验证选择；价格分组不等同 SLA。

适合有真实调用量、能自助接入且有基础技术能力的开发者、小团队和同行渠道。不适合小白、白嫖、低预算试玩、高售后消耗或滥用需求。

## FAQ

### 429 后切到 Gemini 或 GPT 就能恢复吗？

不一定。先判断限流范围、备用路由可用性、能力差异和 deadline；不兼容的 fallback 可能比排队更差。

### Retry-After 超过页面等待预算怎么办？

不要违反等待值做即时重试。返回受控响应、提供异步任务或转人工，并记录剩余预算。

### Timeout 是否可以安全重试？

无法仅凭客户端 timeout 推断上游未处理。先标未知并对账，区分只读生成和真实业务副作用。

### 示例能作为分布式限流器吗？

不能。当前 Python/Node 实现是进程内策略原语。多实例需要共享原子状态或集中 scheduler。

### 选择价格档位时看什么？

除预算外，验证稳定性、延迟、模型能力、业务失败成本和每个有效结果成本；不要把最低单价当作唯一标准。

## 资源与联系

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- 邮箱：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866
