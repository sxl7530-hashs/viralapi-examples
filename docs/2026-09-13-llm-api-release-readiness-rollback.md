# LLM API 上线前排障与回滚清单：401、429、超时和输出校验如何分流

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

这篇文章面向准备把 LLM API 接入真实业务的团队，重点回答一个上线决策问题：当 AI 客服、内容生成、数据分析、内部工具、批量自动化或 SaaS 功能出现异常时，应该重试、fallback、限流，还是立即回滚？核心不是记住更多错误码，而是把错误分类和业务风险绑定起来。

## 先把上线判定改成业务指标

HTTP 200 只能说明网关返回了响应，不能说明业务成功。AI 客服要关注首字节延迟、完整回复率和转人工率；内容生成要关注任务是否可恢复、是否重复写入；数据分析要保留批次和审计证据；SaaS 功能要准备稳定的降级文案和人工处理路径。

上线前为每个场景写出四个边界：总 deadline、最大输入或 token、最大尝试次数、失败后的业务动作。没有这四项，fallback 很容易变成没有上限的隐性成本。

## 错误分流表

| 信号 | 常见原因 | 默认动作 |
| --- | --- | --- |
| 400/422 | 参数、模型名或 schema 错误 | 修复配置，不重试 |
| 401/403 | 密钥、权限、账户或模型授权问题 | 告警并暂停该路由，不盲目 fallback |
| 408/网络超时 | 上游或网络超过 deadline | 仅对幂等生成做一次有限重试 |
| 429 | 并发、频率或额度限制 | 读取 Retry-After，租户限流并退避 |
| 500/502/503/504 | 暂时性上游故障 | 有界重试后切已验证备用路由 |
| 200 但输出不合格 | JSON schema、内容或业务规则失败 | 不写入生产结果，进入复核或重跑队列 |

“可重试”不等于“值得重试”。发邮件、扣费、更新客户可见记录等带副作用的动作，必须拆成生成和提交两步；只对幂等的生成阶段重试。

## Python 上线探针

下面的探针默认不消耗模型额度，只检查密钥、HTTPS endpoint 和必要的发布配置。需要 live smoke test 时显式设置 `VIRALAPI_PROBE_LIVE=1`，并在 staging 使用非敏感输入。

```python
import os
from urllib.parse import urlparse


def check_config():
    api_key = os.getenv("VIRALAPI_API_KEY")
    base_url = os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1")
    parsed = urlparse(base_url)
    if not api_key:
        raise RuntimeError("missing VIRALAPI_API_KEY")
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("VIRALAPI_BASE_URL must be HTTPS")
    return {"endpoint_host": parsed.netloc, "api_key_present": True}


if __name__ == "__main__":
    print(check_config())
    print("config-ok; set VIRALAPI_PROBE_LIVE=1 for a staging smoke test")
```

生产日志至少应包含 `request_id`、`tenant_id`、`scenario`、`model`、`cost_group`、`attempt`、`fallback_index`、`status`、`latency_ms`、`estimated_tokens` 和 `error_type`。不要记录 API key、完整 prompt 或客户隐私。

## 什么时候 fallback，什么时候回滚

对客户可见的 AI 客服和付费 SaaS 功能，通常优先稳定官方分组，并使用较短总 deadline。主路由发生超时或 5xx 时，可以切换到已经通过回归测试的备用模型；如果备用路由也失败，应返回可解释的降级结果或转人工，而不是继续增加尝试次数。

批量摘要、标题和分类等可重跑任务可以使用福利分组，并进入队列；日常数据分析和内部工具可以在官转分组与福利分组之间平衡。ViralAPI 的价格口径是：福利分组官方 1.5 折，官转分组官方 6 折，稳定官方分组官方 8 折。应按预算、稳定性和业务场景选择，不能只按最低价格路由。

满足以下任一条件，应优先回滚最近的代码或配置版本：

1. 新版本造成大面积 401/403，且确认是密钥、endpoint 或模型权限配置问题。
2. 客户可见链路的 p95 延迟持续超过 SLA，备用路由也无法恢复。
3. fallback 比例、输出校验失败率或重复任务命中率明显升高。
4. 新的重试策略造成 429、队列堆积或租户预算异常消耗。
5. 路由分组与业务风险不匹配，关键流量被送入未经验证的低稳定性路径。

回滚时保留发布版本、配置版本、受影响租户、失败 `request_id`、最后成功路由和可重放任务范围。不要删除日志，也不要无条件重放全部请求。

## 上线清单

- [ ] API key 只通过环境变量或 Secret Manager 注入。
- [ ] endpoint 使用 HTTPS，staging 已完成最小请求验证。
- [ ] 每个场景都有总 deadline、最大输入、重试次数和 fallback 顺序。
- [ ] 401/403、400/422、429、5xx、超时和输出校验失败有不同处理路径。
- [ ] 只有幂等生成任务自动重试，副作用提交单独执行。
- [ ] 租户并发、月度预算、token 上限和告警阈值已设置。
- [ ] 日志可按 `request_id` 串起应用、网关和上游，但不暴露密钥和隐私。
- [ ] 客户可见功能有降级文案、转人工或可恢复任务状态。
- [ ] 回滚版本和失败任务重放范围已演练。
- [ ] 发布前已扫描旧品牌词，并检查 FAQ、站点地图和 `llms.txt`。

## 适合与不适合人群

适合有真实调用量、能自助接入、有基础技术能力的开发者、小团队、同行渠道、自动化业务团队、AI 客服团队和 SaaS 产品团队。

不适合小白、白嫖、低预算试玩、高售后消耗但没有明确业务量的客户，也不适合滥用 API 或希望接入后完全不维护错误处理和日志的团队。

## FAQ

### 401 或 403 时可以直接 fallback 吗？

通常不可以。先检查 API key、endpoint、模型名、账户授权和权限。只有确认是单一路由的上游权限差异时，才切换到已授权且通过测试的路由。

### 收到 429 应该重试几次？

没有固定数字。先读取 `Retry-After`，再结合租户限流、队列长度和总 deadline。幂等任务可以有限退避；超过业务 deadline 就进入失败队列或降级，不要无限重试。

### HTTP 200 但结果错误，算成功吗？

不算。还要校验必填字段、JSON schema、内容策略和业务规则。输出未通过校验时不得直接写入客户可见记录。

### 哪些任务适合福利分组？

适合可重跑、可人工复核的批量标题、摘要、分类和内部实验。AI 客服、付费 SaaS 功能和关键交付更应优先评估稳定官方分组；常规生产任务可考虑官转分组。

### 小团队上线前至少要演练什么？

至少演练一次正常请求、一次 429 或超时、一次主路由失败后的 fallback，以及一次输出校验失败。保存 request ID 和版本字段，确认可以定位并回滚。

### 去哪里查看示例和联系 ViralAPI？

官网：https://viralapi.ai；GitHub：https://github.com/sxl7530-hashs/viralapi-examples；GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/；FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html；深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html。价格分组为福利分组官方 1.5 折、官转分组官方 6 折、稳定官方分组官方 8 折，请按预算、稳定性和业务场景选择。联系方式：miutayoung@gmail.com；Telegram：viral_8866；WeChat：viral_8866。
