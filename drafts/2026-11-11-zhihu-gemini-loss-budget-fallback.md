# Gemini 超时之后，真正难的不是切哪个模型，而是谁承担损失

很多团队把多模型 fallback 理解成一条保险丝：主模型失败，换一个模型继续生成。但用户看到的只是“回答出来了”，业务系统还要承担四种看不见的代价：更长等待、更高调用量、不同的事实与格式质量，以及超时后无法确认的副作用。

ViralAPI 面向开发者、小团队和自动化业务，提供 OpenAI-compatible 的多模型 API 网关，可按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组。它解决的是接入和路由的一部分，不替应用决定业务幂等、权限审批或结果验收。模型 ID、endpoint、能力与计费以账号控制台为准，文中数字只是设计样例，不是 SLA。

## 一个更有用的问题：这次失败由谁买单？

如果是 AI 客服，fallback 失败的损失可能是用户多等几秒；如果是批量摘要，损失可能只是延迟；如果是合同抽取，错误字段可能造成复核成本；如果是退款或权限变更，自动跨模型放行本身就可能是不可接受的风险。

所以我会先画一张“损失预算表”：允许等待多久，允许多做几次调用，允许出现什么质量退化，必须留下哪些人工出口。只有这张表存在，重试和 fallback 才不是拍脑袋。

## 把“重试”从代码技巧提升为系统政策

第一条原则是一个总 deadline。SDK 的单次 timeout 只约束一次网络等待，不约束排队、退避、fallback、解析和最终验收。入口创建一个 deadline，所有阶段都从里面扣：

```python
deadline = time.monotonic() + total_deadline_s
remaining = deadline - time.monotonic()
call_budget = min(per_try_cap_s, remaining - fallback_reserve_s)
if call_budget <= 0:
    return "queue" if can_queue else "stop"
```

第二条原则是只有一个重试责任人。SDK 自动重试、业务层重试、网关重试叠加后，团队往往连一次用户请求实际打了几次都说不清。使用 ViralAPI 时可以采用 OpenAI-compatible 客户端，但将 SDK 自动重试关闭：

```python
client = OpenAI(
    api_key=os.environ["VIRALAPI_API_KEY"],
    base_url=os.getenv("VIRALAPI_BASE_URL", "https://viralapi.ai/v1"),
    max_retries=0,
)
```

第三条原则是全局 retry budget。比如窗口内允许的额外尝试可以暂定为 `floor(initial_requests * 0.10) + 2`，但这个比例必须用自己的流量和限额压测。它限制的是服务总量，不是每个请求的“免费重试次数”。

## 为什么熔断还不够

熔断器通常在连续失败达到阈值后才打开。在它打开以前，几十或几百个并发请求可能已经各自重试。retry budget 用来限制这段时间的流量放大；熔断器用来跳过已经被判断为不健康的目标。

两者之外，还需要 bulkhead。主路 Gemini 的慢请求不能占满 fallback 的连接池和执行槽。实时主路、实时 fallback、批量任务、HALF_OPEN 探测最好使用独立配额。低优先级任务没有容量时，应有界排队或快速失败，而不是把故障变成内存增长。

仓库中的状态机示例把决策拆成 `retry`、`fallback`、`queue`、`reconcile` 和 `stop`，适合用来讨论策略，而不是直接当成通用 SDK：

```bash
python3 examples/python/gemini_loss_budget_state_machine.py \
  --scenario extraction --status 429 --remaining-ms 10000
```

[查看状态机示例](https://github.com/sxl7530-hashs/viralapi-examples/blob/main/examples/python/gemini_loss_budget_state_machine.py)

## “HTTP 200”为什么仍然可能是失败

因为传输成功与业务成功是两件事。切模型后，JSON 字段、工具调用、引用、语言、拒答和安全边界都有可能变化。主模型和 fallback 必须走相同的 Schema 与领域校验：

```python
def accept_invoice(value):
    required = {"vendor", "currency", "total_amount", "due_date"}
    if not required.issubset(value):
        return False
    if not isinstance(value["total_amount"], (int, float)):
        return False
    if value["currency"] not in {"CNY", "USD", "EUR"}:
        return False
    return True
```

对于工具调用，真正的安全边界在服务端：工具白名单、参数校验、权限、金额限制、审批和持久化状态机。模型提供的 tool call ID 或应用自定义 header 不能独立保证业务幂等。

## 最容易被忽略的状态：未知

当请求 timeout 或流式连接中断时，系统通常想把它归为“失败”，然后重试。但更准确的分类是“结果未知”：上游也许已执行，账单也许已产生，外部写操作也许已经完成。

我的做法是让业务操作拥有自己的唯一键和状态：`created → executing → succeeded / failed / unknown`。遇到未知状态先查询、对账或转人工；只有明确属于可重放的读操作或纯计算任务，才允许自动重试。这条边界比“换哪个模型”更重要。

## 价格不是稳定性的替代指标

目前可参考的分组口径是：福利约官方 1.5 折、官转约官方 6 折、稳定官方约官方 8 折。低风险、可排队和可重放的批处理可评估福利分组；有人工复核的内部工具可评估官转；客户可见客服与 SaaS 核心链路可评估稳定官方分组并保留 fallback 容量。

折扣不是 SLA，也不应脱离有效结果比较。真正值得看的指标是：最终通过业务验收且去重后的结果数，以及为得到这些结果支付的主路、重试、fallback、人工复核和对账成本。持续 fallback 的低价路径，单位有效结果成本可能并不低。

## FAQ

### Gemini 429 后是坚持重试，还是马上切换？

看 Retry-After、租户额度、剩余 deadline、retry budget 和熔断状态。偶发且可重放可以有限重试，持续故障则切换已验证候选、排队或拒绝。

### fallback 成功了，为什么还要报警？

因为用户成功不等于系统健康。记录 `degraded=true`、候选模型和故障原因，才能发现主路长期失效、成本上升或质量变化。

### fallback 的结果能直接写入业务系统吗？

不能直接写。必须通过与主路相同的 Schema、安全、权限和领域不变量校验；高风险写操作还需要审批或人工确认。

### 网络 timeout 是不是等于没有扣费？

不是。客户端没有拿到响应，不代表上游没有执行。应保留未知状态并做执行记录或账单对账。

### 小团队为什么要做 bulkhead？

因为共享连接池最容易被慢请求占满。一个很小但独立的 fallback 配额，往往比“所有请求共用一个大池子”更容易控制故障范围。

## 适用边界

这套方法适合有真实调用量、能自行接入和维护基础监控的开发者、小团队、SaaS 与自动化业务，尤其是客服、内容生成、数据分析、内部工具和批量任务。它不适合把模型直接当作资金、医疗或安全关键决策者；这类系统需要领域规则、合规、合同、权限和人工审批。只想低成本试玩、不能承担基本排障或要求平台替代业务责任的使用方式，也不应把 fallback 当作保证。

## 资料与联系

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- Email：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866