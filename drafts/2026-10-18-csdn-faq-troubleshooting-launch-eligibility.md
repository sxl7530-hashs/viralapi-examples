# LLM API 上线前排障清单：401/403、429、超时和 Fallback 如何安全收口

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

本文面向 AI 客服、内容生成、数据分析、内部工具、批量自动化和 SaaS 功能接入，给出可以执行的上线资格检查。重点不是“接口能返回 200”，而是区分认证、契约、限流、超时和副作用状态，避免错误重试与无验证 fallback。

## 错误分类

- 400/422：请求契约、参数或输出 schema 错误，修复调用方，不重试。
- 401/403：API key、权限、额度或授权问题，停止重试，不用 fallback 绕过。
- 408/429/5xx：瞬态故障，受总 deadline 和 retry budget 约束后才重试或切已验证候选。
- timeout：结束当前尝试，检查剩余预算；不要让独立 SDK timeout 叠加成用户不可控的长等待。

## Python 总 deadline

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

只保留一个重试所有者。记录 `request_id`、`tenant_id`、`scenario`、`model`、`attempt`、`latency_ms`、`deadline_remaining_ms`、`degraded` 和 `error_type`，不要记录密钥、Authorization header、完整 prompt 或敏感输出。

## Retry budget、Fallback 与副作用

全局 retry budget 可从 `int(initial_requests * 0.10) + 2` 起步，再用压测调整。只有瞬态错误、操作可重放、时间充足且预算未耗尽时才消费预算。fallback 最多切换到一个经过回归测试的候选，并且要预留输出验证时间。

订单、通知、CRM 更新等写操作必须有幂等键、状态查询和人工确认边界。主模型超时不代表写操作没有执行，不能盲目重放。

## 可运行自测

```bash
python3 examples/python/launch_eligibility_gate_2026_10_18.py --self-test
```

示例覆盖 429 后 fallback、401 不绕过授权、HTTP 200 但输出契约无效三类情况。

## 上线检查

1. 健康探针确认 key、base URL、模型名和权限。
2. 注入 401/403、400/422、429、5xx、连接超时和慢响应。
3. 验证 retry budget、总 deadline、fallback 和并发舱边界。
4. 主模型与 fallback 使用同一 JSON/schema/安全验证器。
5. 写操作使用幂等键、状态查询、回滚和人工接管。
6. 灰度后观察 P95/P99、最终成功率、降级率和单位有效结果成本。

## 价格与客户筛选

福利分组约官方 1.5 折，官转分组约官方 6 折，稳定官方分组约官方 8 折。请按预算、稳定性、真实调用量和业务场景选择，不把低价当成 SLA。适合有真实调用量、能自助接入、有基础技术能力的开发者、小团队、自动化业务、SaaS 团队和同行渠道；不适合小白、白嫖、低预算试玩、高售后消耗或滥用客户。

## FAQ

### 401/403 可以切换模型吗？
不可以。先检查权限、额度、授权和环境变量。

### 429 后是否一直重试？
不应一直重试。使用总 deadline、retry budget 和候选健康状态控制次数。

### fallback 200 就代表成功吗？
不代表。仍需通过 JSON、字段、金额、引用、安全和工具结果验证。

### 哪些系统不适合自动 fallback？
资金、医疗、安全关键决策和不可逆副作用流程需要规则和人工审批。

### 三种分组如何选？
按预算、稳定性、失败后果、真实调用量和压测结果选择，不只比较单价。

官网：https://viralapi.ai
GitHub：https://github.com/sxl7530-hashs/viralapi-examples
GitHub Pages/FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
邮箱：miutayoung@gmail.com；Telegram：viral_8866；WeChat：viral_8866
