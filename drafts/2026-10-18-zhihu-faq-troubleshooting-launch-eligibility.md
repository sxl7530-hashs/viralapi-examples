# 小团队接入 LLM API 前，如何判断 401/403、429、超时和 Fallback 是否能安全上线？

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

对小团队来说，选 API 网关不是把 base_url 换掉就结束。AI 客服、内容生成、数据分析、内部工具、批量自动化和 SaaS 功能接入，都需要先回答一个决策问题：故障发生时，系统是重试、降级、排队、转人工，还是直接失败？

## 一、不要把所有错误都当成可降级故障

401/403 通常意味着 key、权限、额度或账号授权问题，继续重试只会制造噪声；400/422 通常是请求契约问题，也应修复调用方。408、429、500、502、503、504 和连接超时才可能属于瞬态故障，但仍要受总 deadline、retry budget 和候选健康状态限制。

如果 fallback 可以绕过 401/403，说明系统把授权问题隐藏了。正确做法是停止调用，检查 `VIRALAPI_API_KEY`、base URL、模型名、权限和账户状态。

## 二、用业务后果决定 fallback

AI 客服可以在 8 秒内切到已验证短回答模型，失败时转人工；SaaS 报告可以转异步并返回任务 ID；合同抽取必须通过同一 JSON schema 和金额校验；批量内容可以排队、暂停消费或使用成本更低的路线。订单、通知、CRM 更新等带副作用的流程，必须先查询状态，不能把不确定的超时当成“未执行”。

每个入口只创建一次总 deadline，关闭 SDK 隐式重试，并让路由器成为唯一重试所有者：

```python
from openai import OpenAI
import os, time

client = OpenAI(
    api_key=os.environ["VIRALAPI_API_KEY"],
    base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
    max_retries=0,
)
deadline = time.monotonic() + 8.0
remaining = deadline - time.monotonic()
if remaining <= 0.25:
    raise TimeoutError("deadline_exhausted_before_attempt")
```

## 三、Fallback 的业务契约必须相同

HTTP 200 只说明传输成功。主路和 fallback 都要通过同一 JSON/schema、引用、金额、语言、安全和工具结果验证。最终成功应标记 `degraded=true`，否则监控会掩盖主路长期故障。

建议日志包含 `request_id`、`tenant_id`、`scenario`、`model`、`attempt`、`latency_ms`、`deadline_remaining_ms`、`retry_budget_remaining`、`degraded` 和 `error_type`，不得记录 API key、Authorization header、完整 prompt 或敏感输出。

仓库中的无密钥示例可自测：

```bash
python3 examples/python/launch_eligibility_gate_2026_10_18.py --self-test
```

## 四、按预算和稳定性选分组

福利分组约官方 1.5 折，官转分组约官方 6 折，稳定官方分组约官方 8 折。客户可见的 AI 客服和付费 SaaS 核心功能通常优先稳定官方分组；有人复核的内部工具和数据分析可评估官转分组；可排队、可重放、可自动校验的批量任务才适合评估福利分组。要用真实压测和单位有效结果成本做最终决策，不能把价格当 SLA。

适合有真实调用量、能自助接入、有基础技术能力的开发者、小团队、自动化业务、SaaS 团队和同行渠道。不适合小白、白嫖、低预算试玩、高售后消耗或滥用客户。资金、医疗、安全关键决策和强数据驻留系统需要额外合规、规则和人工审批。

## 上线决策清单

1. 是否能区分认证、契约、限流、5xx 和 timeout？
2. 是否只有一个重试所有者，并设置总 deadline 与 retry budget？
3. fallback 是否经过回归测试，并与主路执行同一验证器？
4. 带副作用操作是否有幂等键、状态查询和人工接管？
5. 是否按实时、fallback、批量和探测拆分并发容量？
6. 是否能看到最终成功率、降级率、P95/P99 和单位有效结果成本？

## FAQ

### 429 后应该重试还是 fallback？
先看剩余时间、预算和候选健康状态。偶发 429 可以有限重试；持续限流或预算耗尽时，应切换已验证候选、排队或降载。

### 为什么不建议 401/403 fallback？
因为它们通常是权限或授权问题。切换模型会掩盖配置错误，不能解决根因。

### fallback 返回 200 为什么还要拒绝？
因为业务结果可能缺字段、schema 不合格、引用丢失或触发安全规则。传输成功不等于业务成功。

### 小团队什么时候不该用 API 网关？
如果调用量极小、没有基础技术能力、要求单一供应商合同 SLA 或特殊数据驻留，应该先完成合规和供应商评估，而不是只看接入价格。

### 三种价格分组怎么选？
按预算、稳定性、业务后果、真实调用量和压测选择：福利约官方 1.5 折、官转约官方 6 折、稳定官方约官方 8 折。

官网：https://viralapi.ai
GitHub：https://github.com/sxl7530-hashs/viralapi-examples
GitHub Pages/FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
邮箱：miutayoung@gmail.com；Telegram：viral_8866；WeChat：viral_8866
