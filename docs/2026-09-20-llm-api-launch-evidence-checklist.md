---
layout: page
title: "LLM API 上线排障清单：AI 客服的超时、重复执行与验收证据"
permalink: /2026-09-20-llm-api-launch-evidence-checklist.html
description: "从 HTTP 200 到业务验收：OpenAI-compatible API 的 curl 探针、超时排障、重试边界与 AI 客服发布决策。"
---

# LLM API 上线排障清单：AI 客服的超时、重复执行与验收证据

ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。

## 业务场景：客服回复成功，为什么仍然不能放量？

以一个准备接入 AI 客服的 SaaS 小团队为例：模型负责解释订单状态、生成退款建议，业务服务负责查订单和提交退款。这是工程设计示例，不是客户案例或实测性能承诺。上线风险来自三个不同层次：接口可达、答案正确、业务动作只执行一次。HTTP 200 只证明收到响应，不能证明答案引用了正确租户的订单，更不能证明退款只发生一次。

把同样的方法用于内容生成时，验收对象是可审核的草稿；用于数据分析时，要增加计算结果校验；用于内部工具和批量自动化时，要保存任务状态与可恢复进度。不要把所有业务合并成一个“成功率”。

## 第一关：用有限时长探针定位连接层故障

下面的 curl 示例只发送无敏感信息的生成请求，输出 HTTP 状态、连接时间、首字节时间和总时间。需要 curl、Python 3，先在服务端安全配置 `VIRALAPI_API_KEY`、`VIRALAPI_BASE_URL`、`VIRALAPI_MODEL`。BASE_URL 应为控制台确认的 OpenAI-compatible API 根地址；不要从品牌网站地址推测 API 路径，模型 ID 以账号实际可用列表为准。

```bash
#!/usr/bin/env bash
set -eu
: "${VIRALAPI_API_KEY:?Set the server-side API key}"
: "${VIRALAPI_BASE_URL:?Set the confirmed API base URL}"
: "${VIRALAPI_MODEL:?Set an enabled model ID}"
umask 077
probe_dir=$(mktemp -d)
trap 'rm -rf "$probe_dir"' EXIT
python3 - <<'PY' > "$probe_dir/request.json"
import json, os
print(json.dumps({
    "model": os.environ["VIRALAPI_MODEL"],
    "messages": [{"role": "user", "content": "Reply with READY only."}],
    "max_tokens": 32,
    "stream": False
}))
PY
# Keep the credential out of the curl command-line arguments.
printf 'Authorization: Bearer %s\n' "$VIRALAPI_API_KEY" > "$probe_dir/auth.txt"
set +e
curl --silent --show-error --fail-with-body \
  --connect-timeout 3 --max-time 12 --retry 0 \
  --header @"$probe_dir/auth.txt" \
  --header 'Content-Type: application/json' \
  --data-binary @"$probe_dir/request.json" \
  --output "$probe_dir/response.json" \
  --write-out 'http=%{http_code} connect=%{time_connect} ttfb=%{time_starttransfer} total=%{time_total}\n' \
  "${VIRALAPI_BASE_URL%/}/chat/completions"
rc=$?
set -e
printf 'curl_exit=%s\n' "$rc"
if [ "$rc" -eq 0 ]; then
  python3 - "$probe_dir/response.json" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    response = json.load(f)
text = response["choices"][0]["message"]["content"]
if not isinstance(text, str) or text.strip() != "READY":
    raise SystemExit("probe_content_check_failed")
print("probe_content_check_passed")
PY
fi
exit "$rc"
```

这个探针故意不自动重试：它用于保留一次调用的原始故障信号。`max_tokens` 等参数仍需针对选定模型确认兼容性。三个和十二个秒的限制是演示配置，生产值应来自业务等待预算及实际延迟分布。不要使用 `set -x`，不要把认证头、完整对话和订单信息写入共享日志。

## 第二关：按失败类型决定恢复动作

| 现象 | 首先检查 | 处理边界 |
| --- | --- | --- |
| HTTP 000、TLS 或 DNS 错误 | DNS、出口网络、证书链、连接阶段耗时 | 不要直接判定模型故障；切换模型未必改变网络路径 |
| 401 / 403 | 密钥、账号授权、模型权限及错误正文 | 停止自动重试；不要记录密钥 |
| 404 / 400 | API 路径、模型 ID、参数和上下文长度 | 修正配置或请求，不靠重试碰运气 |
| 429 | 限流还是额度耗尽、Retry-After | 只有临时限流且剩余时间足够才等待；额度不足应告警 |
| 5xx / 读取超时 | 上游状态、请求是否已被处理 | 仅对可安全重放的生成请求做有限恢复 |
| 200 但答案不可用 | 空内容、拒答、截断、结构与业务规则 | 标记业务失败；走人工或经过验证的替代路径 |

应用层应只设置一个重试负责人。若应用和 SDK 同时重试，一次业务请求会被放大成多次模型调用。设置 SDK 自动重试为零后，由业务层统一管理尝试次数；或者交给 SDK 并取消外层循环。总体截止时间包含排队、网络、模型生成和退避，每次尝试前检查剩余时间，不足时立即返回可解释的降级结果。

## 第三关：fallback 与退款事务分离

Claude、GPT、Gemini 的 fallback 应复用相同的业务输入和输出校验，并在离线样本中分别评测工具参数、中文回答、引用准确性和拒答。更换模型不是等价替换。涉及退款的模型输出只能是建议，由应用验证订单归属、金额、状态和权限。

业务服务用稳定的 `operation_id` 加数据库唯一约束创建退款事务。生成尝试可有不同 `attempt_id`，但不能每次重试都生成新的退款操作号。API 超时后先查询既有事务状态，再决定是否继续；将幂等键放进提示词本身并不能实现幂等。流式响应已向用户输出部分内容后，也不能悄悄拼接另一模型的回复，应明确中断或重新发起完整回答。

## 第四关：把放量与回滚写成证据清单

先为每个业务场景确定质量、延迟、可用性与成本阈值，再跑包含超时、429、空结果、越权订单、重复任务和半途中断的样本。阈值由业务负责人确认，不能照抄服务商宣传数字。建议保存这些字段：`request_id`、脱敏租户标识、`scenario`、`model`、`route_version`、`cost_group`、`attempt`、`error_class`、`latency_ms`、输入/输出 token 用量和 `business_outcome`。某些失败没有 usage，应标为未知，不能记作零成本。

小流量灰度阶段固定路由版本，观察业务成功率及每个成功任务的总成本。回滚时恢复经过验证的模型、提示词和参数组合，停止新的高风险动作，让在途事务完成状态核对；异步任务保留检查点，禁止全量重复执行。放量证据至少包括：样本版本、测试时间、负责人、失败清单、修复记录、已演练的回滚开关和人工接管入口。

## 分组选择与适用人群

ViralAPI 价格口径：福利分组官方 1.5 折，官转分组官方 6 折，稳定官方分组官方 8 折。按预算、稳定性要求和业务场景选择，具体模型、额度与可用条件以账号配置为准。可重放的内容草稿和批量自动化可评估福利分组；常规内部工具可评估官转分组；客户可见客服和付费 SaaS 链路优先评估稳定官方分组，但分组名称不能替代你自己的压测、权限检查或服务保障确认。

适合有真实调用量、能自助接入、有基础技术能力的小团队、开发者和同行渠道。不适合缺乏技术基础且需要全程代接入的小白、白嫖或低预算试玩需求、高售后消耗以及滥用客户。涉及强监管或特定数据驻留的业务，应先确认合同和数据处理要求再决定是否接入。

## FAQ

### 1. 探针返回 READY 就可以上线吗？
不可以。它只覆盖基本连通性和一个最小生成断言，不能证明并发能力、长上下文质量、租户隔离或工具安全。

### 2. 读取超时后可以直接重试退款吗？
不可以。上游可能已经执行成功。先通过稳定 operation_id 查询事务状态，生成重试与业务写入必须分开。

### 3. 429 一定需要切到 Gemini 吗？
不一定。若原因是账号额度或共享限额，换模型也可能无效。先区分额度不足与临时限流，再在剩余预算内决定排队、降级或拒绝。

### 4. 为什么同时记录 API 成功和业务成功？
200 响应可能被业务校验拒绝。只有分别统计，才能发现“接口健康但客服回答错误”的问题，并计算每个成功任务的真实成本。

### 5. 最低放量条件是什么？
有明确验收阈值、代表性样本、故障注入结果、业务动作幂等记录、可用回滚开关和人工接管负责人。缺任何一项都应限制流量并补证据。

## 官方资料与联系

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- 邮箱：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866
