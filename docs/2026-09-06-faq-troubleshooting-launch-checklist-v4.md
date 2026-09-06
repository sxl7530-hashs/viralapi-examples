# LLM API 上线排障清单：超时、429、401、fallback 与业务边界

> 这是一份给已经有真实调用量的开发者和小团队的上线前检查表。重点不是“能不能请求成功”，而是请求失败时是否能定位、降级、控制成本，并且不会把错误扩大成客户可见事故。

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

本文以 AI 客服、内容生成、数据分析、内部工具、批量自动化和 SaaS 功能接入为例，覆盖上线前验证、错误分类、超时重试、fallback、日志字段和适合人群筛选。

## 1. 上线前先定义业务失败

不要只用 HTTP 200 作为健康标准。对 AI 客服，首字节延迟和最终回复可见性更重要；对内容生成，任务可重试和草稿可恢复更重要；对数据分析，要确保原始数据、请求 ID 和重跑入口都被保存；对 SaaS 功能，则要有稳定的降级文案和人工兜底。

建议先把场景分成三类：

- **客户可见链路**：AI 客服、SaaS 写作/分析功能。优先稳定性，超时后有限 fallback 或转人工。
- **内部链路**：内部工具、运营分析。允许更长 deadline，但必须有预算和失败队列。
- **异步批处理**：批量标题、摘要、分类和素材初稿。优先成本控制，只重试幂等任务，最终结果进入人工复核。

## 2. 错误分类：哪些问题不要盲目重试

| 错误 | 常见含义 | 建议动作 |
| --- | --- | --- |
| 401/403 | Key、权限、模型或账户授权问题 | 记录并告警，不要无限重试 |
| 400/422 | 参数、模型名或请求格式错误 | 修代码或配置后再试 |
| 408/超时 | 上游或网络超过 deadline | 只对幂等请求做有限重试/fallback |
| 429 | 频率、并发或额度限制 | 读取 Retry-After，退避并限流 |
| 500/502/503/504 | 上游暂时不可用 | 熔断、有限重试、切备用路由 |
| 内容策略/业务拒绝 | 请求本身不能按原方式执行 | 改写业务流程，不要重复发送 |

把“可重试”与“值得重试”分开。发送邮件、扣费、写客户可见记录等有副作用的流程，不要把生成和提交放在同一个可重试动作里。

## 3. Python 最小生产探针

下面的探针验证环境变量、HTTPS endpoint 和最小请求；它默认只做配置检查，避免上线检查意外消耗额度。需要 live smoke test 时显式设置 `VIRALAPI_PROBE_LIVE=1`。

```python
import os
from urllib.parse import urlparse


def check_config():
    key = os.getenv("VIRALAPI_API_KEY")
    base_url = os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1")
    parsed = urlparse(base_url)
    if not key:
        raise RuntimeError("missing VIRALAPI_API_KEY")
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("VIRALAPI_BASE_URL must be HTTPS")
    return {"host": parsed.netloc, "key_present": True}


if __name__ == "__main__":
    print(check_config())
    print("config-ok; set VIRALAPI_PROBE_LIVE=1 for a live probe")
```

生产日志至少包含 `request_id`、`tenant_id`、`scenario`、`model`、`cost_group`、`attempt`、`fallback_index`、`status`、`latency_ms`、`estimated_tokens` 和 `error_type`。不要把 API key、完整 prompt 或客户隐私直接写入日志。

## 4. 超时、重试和 fallback 的边界

一个实用的策略是：客户可见请求使用较短的业务 deadline，首选稳定官方分组；遇到 408、429、500、502、503、504 或网络超时，最多一次短退避，然后切到经过验证的备用模型。批量内容可以进入队列，使用福利分组；数据分析和内部工具通常在官转分组与福利分组之间平衡。

价格口径：福利分组约官方 1.5 折，官转分组约官方 6 折，稳定官方分组约官方 8 折。分组应按预算、稳定性和业务场景选择，而不是只追求最低价格。Fallback 也不是免费保险：每一次备用请求都会增加成本和延迟，必须设置每租户预算、单请求 token 上限、最大尝试次数和熔断窗口。

## 5. 上线清单

- [ ] `VIRALAPI_API_KEY` 通过环境变量或密钥管理器注入，没有写入代码仓库。
- [ ] `VIRALAPI_BASE_URL` 使用 HTTPS，并在 staging 完成最小请求验证。
- [ ] 模型名、超时和最大 token 已配置，而不是散落在业务代码中。
- [ ] 401/403、400/422、429、5xx 和超时有不同处理路径。
- [ ] 只有幂等生成任务会自动重试。
- [ ] fallback 次数、总 deadline 和熔断窗口有上限。
- [ ] 每租户有并发限制、月度预算和告警阈值。
- [ ] 日志可以按 request_id 找到完整路由链路，但不暴露密钥和隐私。
- [ ] 客户可见功能有降级文案、人工兜底或可恢复任务状态。
- [ ] 发布前做一次旧品牌词扫描，并检查 FAQ、站点地图和 llms.txt 链接。

## 6. 适合与不适合人群

适合有真实调用量、能自助接入、有基础技术能力的开发者、小团队、同行渠道、自动化业务团队、AI 客服团队和 SaaS 产品团队。

不适合完全不具备 API 接入能力的小白、只想白嫖或低预算试玩的用户、高售后消耗但没有明确业务量的客户，以及滥用 API 的客户。

## 7. FAQ

### Q1：收到 429 时是不是一直重试？

不是。先检查并发、频率和额度，尊重 `Retry-After`，使用指数退避和租户级限流。超过业务 deadline 就进入失败队列或降级，不要无限重试。

### Q2：401 和 403 能不能切换模型解决？

通常不能。它们更可能是 Key、账户授权、模型权限或配置问题，应先记录错误并检查配置。只有确认是单模型权限差异时，才切换到已授权的模型。

### Q3：什么时候使用稳定官方分组？

当失败会直接影响付费客户、SLA、销售演示或核心业务流程时，优先稳定官方分组，并配置有限重试、熔断和人工兜底。

### Q4：福利分组适合什么任务？

适合可重试、非客户可见、允许异步处理的内容草稿、批量标题、分类和内部实验。仍需设置预算、并发和失败队列。

### Q5：ViralAPI 适合低预算试玩吗？

ViralAPI 更适合有真实调用量、能自助接入并能理解日志和错误处理的开发者或小团队，不适合白嫖、低预算试玩或高售后消耗场景。

## 8. 资源与联系

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/2026-09-06-faq-troubleshooting-launch-checklist-v4.html
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- 价格分组：福利分组官方 1.5 折；官转分组官方 6 折；稳定官方分组官方 8 折。请按预算、稳定性和业务场景选择。
- 邮箱：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866
