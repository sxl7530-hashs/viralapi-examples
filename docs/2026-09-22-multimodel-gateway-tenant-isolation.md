---
title: "小团队 Claude/GPT/Gemini 统一网关：租户隔离、原子准入与副作用边界"
description: "从可信租户身份到分布式预算预留、并发租约、超时未决费用和幂等业务提交，补齐多模型 SaaS 的应用侧边界。"
date: 2026-09-22
permalink: /multimodel-gateway-tenant-isolation.html
---

# 小团队 Claude/GPT/Gemini 统一网关：租户隔离、原子准入与副作用边界

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

[9 月 15 日架构文章](2026-09-15-small-team-multimodel-api-gateway-architecture.md)介绍了统一调用和成本路由。这篇继续追问：同一套 SaaS 被多个客户同时使用时，谁有权消费哪个租户的预算？超时后能否释放预算？模型给出退款建议后，谁能真正执行退款？这些问题需要应用侧的可验证状态转换；统一请求格式本身并不回答它们。

## 从一个 AI 客服事故看边界

设想一个为电商商家提供 AI 客服的小团队。商家 A 正在导入历史工单，商家 B 的付费用户同时咨询退款。两者共用应用服务和上游账户。如果批处理占满所有连接，即使 B 还有月度余额，在线客服也会等待；如果浏览器提交的 `tenant_id` 可以直接用于查询，A 甚至可能读到 B 的工单。

客服生成回复草稿、夜间工单摘要和实际发送消息应当分成不同阶段。在线草稿可以优先选择稳定性导向的 Claude、GPT 或 Gemini 路由；摘要进入有界队列；退款和发信只由业务执行层处理。这里的模型家族名称不是可直接复制的 API model ID，实际 ID、可用参数和分组接入方式要从账户配置与接入文档确认。

```text
认证会话 → 服务端解析租户与角色 → 工单/检索权限检查
  → 应用侧原子预算和并发准入 → 已验证的模型路由 → ViralAPI
  → 候选内容验证 → 持久化候选结果
  → 业务授权 + 幂等提交 → outbox → 发信/退款执行器
```

## 租户隔离从身份开始，覆盖数据与路由

应用从经过验证的登录会话或服务凭证解析租户，再检查用户是否有权访问具体工单。不要相信请求体或浏览器的 `X-Tenant-ID`。缓存、向量检索命名空间、数据库查询、队列任务、附件访问和日志查询都必须带同一可信租户作用域。缓存键还需要权限范围、知识库版本、提示词版本和路由版本，避免同租户中不同角色共享不该看到的答案。

ViralAPI 网关不会因为应用随意添加 `X-Tenant-ID` 或 `X-Cost-Group` 就自动执行你的租户隔离、预算、并发或分组选择策略。除非接入文档明确约定且已验证，这类自定义头只能视为应用元数据，不能作为服务端执行承诺。应用侧路由器负责授权和准入；ViralAPI 提供上游模型接入边界。实际分组凭证和路由绑定使用账户支持的配置，密钥仅保留在服务端。

租户也不能从请求参数任意指定 base URL、密钥、model 或高价分组。服务端策略根据租户权限和场景选出允许的路由；跨模型 fallback 仍要遵守数据处理地域、工具权限和输出契约。OpenAI-compatible 不代表模型的工具调用、结构化输出或数据处理条件完全一致。

## 准入必须是原子的分布式操作

“先读余额，再调用，再扣费”在多 worker 下会超支；进程内计数器也无法限制其他实例。预算和并发需要共享持久状态，并通过数据库事务或正确设计的 Redis 原子脚本完成检查与预留。若租户额度和账户总额度分别存储，必须有一致性方案；不能将两次各自成功的检查当成一次全局原子准入。

准入应同时满足这些应用不变量：

```text
已结算费用 + 在途预留 + 本次预留 <= 租户预算
账户已结算费用 + 账户在途预留 + 本次预留 <= 账户预算
租户有效并发租约数 < 租户上限
账户有效并发租约数 < 账户上限
同一 (tenant_id, operation_id, attempt_id) 只能预留一次
```

费用用最小货币单位整数或定点数，记录价格版本和预算周期。预留覆盖输入上限、输出上限及该路由其他可计费项目；单纯字符数估算不是硬预算保证。无法给出保守上界时，应明确这是软预算，设置风险余量并限制请求大小。fallback 的每个实际尝试都需要单独预留，或在首次准入时预留整个尝试计划，不能让两个可能同时计费的请求共享一笔额度。

一次事务写入预留记录、唯一尝试键和带期限的并发租约。重复提交返回已有状态，不再扣一次。明确未发送的请求可以回滚；成功响应按可核对的 usage 结算并释放多余预留。超时、断连、worker 崩溃或响应缺失进入 `unknown` 状态，不能按零费用释放。结算也需要唯一键和条件更新，重复回调不能重复记账。预算跨月时，仍按预留所属周期完成核对。

并发租约和资金预留需要不同生命周期：租约心跳、到期回收与 fencing token 防止旧 worker 修改新持有者的状态；资金预留等待账单或人工核对，不能随租约 TTL 一起删除。租约到期不证明上游推理已经终止，fencing token 也不能阻止上游继续计算。因此还需记录未决请求、限制未决数量并保留容量余量，不能宣称应用租约严格限制了上游实际并发。

给在线客服预留预算和调度份额，批处理使用独立有界队列及每租户公平调度。并发耗尽可返回应用自己的限流结果，预算耗尽返回独立错误码；不要把它们混成上游 429。准入存储不可用时暂停新的计费工作或转人工，而不是绕过校验。

## Python：只演示一次生成的错误边界

以下 Python 3.11+ 代码是文档内的调用切片，不实现认证、原子准入、账本、fallback 或业务执行器。调用方必须先完成授权和预留，再根据返回类别维护账本。示例不自动执行模型请求；只有调用 `generate_candidate` 才会访问网络。安装与账户已验证的 `openai`、`httpx` SDK 后，配置 `VIRALAPI_API_KEY`、`VIRALAPI_BASE_URL` 和 `VIRALAPI_MODEL`；不假定固定 endpoint 或模型名。

```python
import asyncio
import os

import httpx
from openai import AsyncOpenAI, APIConnectionError, APIStatusError, APITimeoutError


def classify_status(status):
    if status in (401, 403):
        return "auth_or_permission"
    if status == 429:
        return "rate_or_quota"
    if status == 408 or 500 <= status < 600:
        return "upstream_transient_candidate"
    if 400 <= status < 500:
        return "request_or_policy"
    return "unexpected_status"


async def generate_candidate(messages, deadline):
    # deadline is the caller's absolute event-loop deadline, including queue time.
    loop = asyncio.get_running_loop()
    if deadline <= loop.time():
        return {"kind": "deadline_before_send"}
    async with AsyncOpenAI(
        api_key=os.environ["VIRALAPI_API_KEY"],
        base_url=os.environ["VIRALAPI_BASE_URL"],
        max_retries=0,
        timeout=httpx.Timeout(8.0, connect=2.0, pool=1.0),
    ) as client:
        try:
            async with asyncio.timeout_at(deadline):
                response = await client.chat.completions.create(
                    model=os.environ["VIRALAPI_MODEL"],
                    messages=messages,
                    stream=False,
                )
        except (TimeoutError, APITimeoutError):
            return {"kind": "timeout_unknown"}
        except APIConnectionError:
            return {"kind": "transport_unknown"}
        except APIStatusError as exc:
            return {"kind": classify_status(exc.status_code),
                    "status": exc.status_code}
        usage = response.usage.model_dump() if response.usage else None
        if not response.choices:
            return {"kind": "invalid_output", "usage": usage}
        choice = response.choices[0]
        message = choice.message
        text = message.content
        if (choice.finish_reason != "stop" or message.tool_calls
                or not isinstance(text, str) or not text.strip()):
            return {"kind": "invalid_output", "usage": usage}
        return {"kind": "candidate", "text": text, "usage": usage}
```

`max_retries=0` 关闭 SDK 自动重试，使每次计费尝试都由应用准入。HTTPX timeout 限制网络阶段等待，不能单独代表完整请求总时限；外层绝对 deadline 覆盖本次 API 等待，调用方还要为校验和持久化留出时间。取消等待不保证上游取消，也不保证免计费。进程终止、外部取消、配置异常及 SDK 解码异常可向外传播，worker 的持久状态恢复机制必须将已发送但未结算的尝试保留为未决。

示例只检查最基本的文本形状；返回 `candidate` 不代表答案正确或允许发送。生产配置必须增加经该模型验证的输出上限，匹配预留估算，并执行租户业务规则、引用依据与内容校验。usage 缺失时保持待核对状态，输出无效但有 usage 的尝试仍需计费结算。

## 错误分类决定是否重新准入

| 结果 | 应用动作 | 费用与副作用边界 |
| --- | --- | --- |
| 本地预算或并发拒绝 | 排队、降级到人工或明确拒绝 | 未发送，无模型费用；不进入 fallback 绕过限额 |
| 401 / 403 | 修复凭证、权限或路由配置 | 不盲目重试，不切换身份规避权限 |
| 429 | 区分短期限流、账户配额和余额问题 | `Retry-After` 可用时结合剩余 deadline；没有余额不能靠换模型解决 |
| 408 / 5xx | 仅作为暂态候选，检查错误语义和故障域 | 不能仅凭状态码断言零费用或自动允许重试 |
| 超时 / 断连 | 保留未决尝试，转人工或对纯生成受控重试 | 上游可能已完成；新尝试需要新预留 |
| 其他 4xx / 内容无效 | 修正请求、权限或业务契约 | 不默认跨模型重放；无效输出也可能收费 |

如需 fallback，最多允许一个经过回归验证的候选，并重新检查租户额度、账户容量和原始 deadline。共享账户限流时跨模型切换可能仍然失败；不要用 fallback 绕过租户准入。已经向用户流式输出的半段回答不能直接拼接另一模型的回答，本例因此限定为非流式候选生成。

## 业务副作用必须有自己的提交协议

退款工单先生成建议，再验证订单归属、退款额度和操作权限。执行器重新检查当前业务状态，不能把模型的 tool call 当成授权。客户端重复点击应复用稳定的业务 `operation_id`；模型 `attempt_id` 随重试改变，不能当业务幂等键。

在同一数据库事务中，以 `(tenant_id, operation_id, action_type)` 唯一约束持久化已授权动作和 outbox 事件，并绑定请求内容摘要；同键不同内容必须拒绝。worker 重复投递 outbox 时继续使用相同下游幂等键。仅靠本地唯一记录无法保证外部邮件或支付恰好一次：如果下游已完成但回执丢失，应查询下游状态；下游不支持幂等或状态查询时暂停自动重发并人工核对。

模型 fallback 只能重新生成未提交的候选，不能重放整段“生成→发信→退款”工作流。迟到模型响应也要检查 operation 的当前状态和版本，不能覆盖已提交结果。

## 用事故演练验证隔离

上线前在本地或测试环境验证：A 伪造 B 的租户头仍被拒绝；两个 worker 同时抢最后一笔预算只能有一个成功；重复 attempt 只预留一次；模型超时后资金仍未决；租约过期后旧 worker 无法结算新尝试；外部动作成功但回执丢失时不会自动重复执行。让 A 批处理饱和，同时观察 B 在线客服的排队延迟，证明公平性策略确实生效。

按租户和场景记录 admission reason、operation/attempt ID、policy/price version、预留与实际费用、未决年龄、并发租约、路由、deadline 剩余量、错误类别与业务提交状态。日志只保留必要标识和汇总，不写密钥、完整提示词或客户工单。衡量“每个已验证并提交的业务结果成本”，同时跟踪未决费用和人工接管率。

## 分组选择与适用人群

价格口径：福利分组官方 1.5 折，官转分组官方 6 折，稳定官方分组官方 8 折。福利分组可评估用于可排队重跑的摘要；官转分组可评估用于日常内部分析；稳定官方分组可作为付费客服链路的评估起点。选择依据是预算、稳定性需求和场景；折扣与分组名称不保证可用性、延迟、容量、隔离能力或业务结果，实际可用模型与计费以账户确认信息为准。

适合有真实调用量、能自助接入和排障的技术小团队、开发者、自动化业务及同行渠道。不适合缺乏 API 基础的小白、白嫖需求、低预算试玩、高售后依赖或滥用场景。

## FAQ

### 1. 添加租户 header 就能隔离客户吗？

不能。可信身份、资源授权和应用侧原子准入必须由服务端执行，自定义 header 不会自动创建网关侧策略。

### 2. 用 Redis 计数就足够了吗？

不够。检查、预算预留和并发租约需要满足原子性与恢复语义，还要覆盖账户总额度、重复请求、持久性和跨周期结算。

### 3. 超时后可以立即释放费用预留吗？

只有能证明未发送或未计费才可以。否则保留未决费用并核对；本地等待取消不等于上游执行取消。

### 4. Claude 切到 GPT 或 Gemini 会改变授权边界吗？

不应改变。fallback 必须仍满足租户权限、数据处理约束、输出契约、预算和总 deadline，并为新尝试重新准入。

### 5. 有业务幂等键就能保证退款恰好一次吗？

不能仅凭一个字符串保证。需要数据库唯一性、授权状态、outbox、下游幂等支持及未知结果核对，且不同内容不能复用同键。

## 资料与联系

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- 本文固定链接：https://sxl7530-hashs.github.io/viralapi-examples/multimodel-gateway-tenant-isolation.html
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 邮箱：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866
