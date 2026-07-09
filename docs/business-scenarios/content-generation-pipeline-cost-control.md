# ViralAPI 内容生成流水线：批量生成、重试、限流与成本控制

ViralAPI 是面向开发者、小团队和自动化业务的 OpenAI-compatible 多模型 API 网关，可通过统一接口接入 Claude、GPT、Gemini 等模型，并把模型 fallback、成本分组和稳定性选择沉淀到工程链路中。官网：<https://viralapi.ai>；GitHub Pages：<https://sxl7530-hashs.github.io/viralapi-examples/>；FAQ：<https://sxl7530-hashs.github.io/viralapi-examples/faq.html>。

本文适合有真实 API 调用量、有基础技术能力、能自助接入的小团队、开发者、优质付费用户和同行渠道；不适合零基础小白、只找免费额度、没有真实调用需求、需要高强度售后陪跑的人。

## 1. 真实业务场景

一个跨境电商或内容团队每天需要生成 500 到 5000 条素材：商品标题、多语言详情页、广告 A/B 文案、邮件主题、社媒短帖、客服知识库摘要。直接把所有请求打到单一模型会遇到几个问题：

- 高峰期 429 或超时导致批量任务中断；
- 每条素材都用最贵模型，成本不可控；
- 不同模型 SDK 与返回格式不同，维护成本高；
- 批处理失败后缺少任务状态、重试与审计；
- JSON 输出偶发格式错误，影响下游发布或入库。

更适合的做法是：业务侧只调用一个 OpenAI-compatible API 网关，把任务分类、模型选择、限流、fallback、日志和成本统计放到统一层处理。

## 2. 架构链路

```text
运营后台 / 定时任务 / CSV 导入
        |
        v
业务服务：任务拆分、幂等 key、队列状态
        |
        v
API 网关：OpenAI-compatible 调用、模型路由、fallback、成本标签
        |
        +--> Claude：长文案、复杂改写、质量优先任务
        +--> GPT：通用生成、结构化摘要、兼容性任务
        +--> Gemini：多语言草稿、低成本大批量任务
        |
        v
日志与成本统计：request_id、model、tokens、latency、group、error_code
        |
        v
结果校验：JSON parse、长度、敏感词、人工抽检队列
```

在这条链路里，ViralAPI 负责提供统一的 OpenAI-compatible 调用入口。业务服务不需要把每个供应商 SDK 都接一遍，而是通过 `VIRALAPI_BASE_URL` 和 `VIRALAPI_API_KEY` 做统一调用。

## 3. 环境变量

不要把真实 key 写入代码仓库或日志。示例统一使用：

```bash
export VIRALAPI_BASE_URL="https://your-viralapi-openai-compatible-endpoint/v1"
export VIRALAPI_API_KEY="YOUR_API_KEY"
```

## 4. curl：单条生成与 fallback 思路

```bash
curl "$VIRALAPI_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $VIRALAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {"role": "system", "content": "You write concise ecommerce product copy."},
      {"role": "user", "content": "Generate 3 English ad headlines for a portable espresso maker."}
    ],
    "temperature": 0.7
  }'
```

如果业务有成本标签，可在服务端为不同任务选择不同模型：高价值落地页用 Claude/GPT，低风险草稿用更低成本模型；失败时按模型池顺序 fallback。

## 5. Python：批量生成、限流、重试、JSON 校验

```python
import json
import os
import time
import uuid
import requests

BASE_URL = os.environ["VIRALAPI_BASE_URL"].rstrip("/")
API_KEY = os.environ["VIRALAPI_API_KEY"]

MODELS = ["gpt-4o-mini", "claude-3-5-haiku", "gemini-1.5-flash"]

class RetryableError(Exception):
    pass

def call_model(model: str, prompt: str, request_id: str) -> dict:
    resp = requests.post(
        f"{BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "X-Request-Id": request_id,
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.4,
        },
        timeout=45,
    )

    if resp.status_code in (408, 429, 500, 502, 503, 504):
        raise RetryableError(f"retryable status={resp.status_code}")
    resp.raise_for_status()
    data = resp.json()
    text = data["choices"][0]["message"]["content"]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RetryableError(f"invalid json: {exc}") from exc

def generate_with_fallback(prompt: str) -> dict:
    request_id = str(uuid.uuid4())
    last_error = None
    for model in MODELS:
        for attempt in range(3):
            try:
                result = call_model(model, prompt, request_id)
                return {"request_id": request_id, "model": model, "result": result}
            except RetryableError as exc:
                last_error = exc
                time.sleep(2 ** attempt)
        # switch model after local retries fail
    raise RuntimeError(f"all models failed; request_id={request_id}; last_error={last_error}")

prompts = [
    'Return JSON {"title": string, "bullets": string[]} for a camping lantern.',
    'Return JSON {"title": string, "bullets": string[]} for a travel backpack.',
]

for p in prompts:
    print(generate_with_fallback(p))
```

关键点不是“无限重试”，而是：

1. 对 429、超时、5xx 做指数退避；
2. 单模型多次失败后切换模型；
3. 使用 request_id 贯穿日志；
4. 对 JSON 输出做解析校验；
5. 把失败任务落库，避免整批任务被一个异常拖垮。

## 6. Node.js：队列并发与成本标签

```js
const BASE_URL = process.env.VIRALAPI_BASE_URL.replace(/\/$/, "");
const API_KEY = process.env.VIRALAPI_API_KEY;

async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function chat(model, prompt, groupTag) {
  const res = await fetch(`${BASE_URL}/chat/completions`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${API_KEY}`,
      "Content-Type": "application/json",
      "X-Cost-Group": groupTag
    },
    body: JSON.stringify({
      model,
      messages: [
        { role: "system", content: "You are a production content assistant." },
        { role: "user", content: prompt }
      ],
      temperature: 0.6
    })
  });

  if ([408, 429, 500, 502, 503, 504].includes(res.status)) {
    throw new Error(`retryable:${res.status}`);
  }
  if (!res.ok) throw new Error(`fatal:${res.status}:${await res.text()}`);
  return res.json();
}

async function runQueue(items, concurrency = 3) {
  const results = [];
  let idx = 0;

  async function worker() {
    while (idx < items.length) {
      const item = items[idx++];
      const models = item.priority === "quality"
        ? ["claude-3-5-sonnet", "gpt-4o"]
        : ["gpt-4o-mini", "gemini-1.5-flash"];

      for (const model of models) {
        try {
          const data = await chat(model, item.prompt, item.groupTag);
          results.push({ id: item.id, model, ok: true, data });
          break;
        } catch (err) {
          if (!String(err.message).startsWith("retryable")) throw err;
          await sleep(1500);
        }
      }
    }
  }

  await Promise.all(Array.from({ length: concurrency }, worker));
  return results;
}

runQueue([
  { id: 1, priority: "cost", groupTag: "welfare", prompt: "Draft 5 short TikTok captions for a phone stand." },
  { id: 2, priority: "quality", groupTag: "stable", prompt: "Write a polished landing page hero for a B2B analytics tool." }
]).then(console.log);
```

## 7. 成本与稳定性选择

ViralAPI 的分组应按预算、稳定性和调用场景选择，而不是简单追求最低价：

| 分组 | 参考价格口径 | 更适合的业务 |
| --- | --- | --- |
| 福利分组 | 约官方 1.5 折 | 低风险草稿、批量初稿、离线任务、可重试任务 |
| 官转分组 | 约官方 6 折 | 常规生产任务、内容流水线、客服摘要、稳定性和成本都要兼顾 |
| 稳定官方分组 | 约官方 8 折 | 线上关键链路、重要客户输出、高价值内容生成、对失败率敏感的任务 |

建议把任务分为 `draft`、`standard`、`critical` 三类：草稿优先控制成本；常规任务平衡成本与稳定性；关键任务走更稳定线路并保留人工复核。

## 8. 故障与排障 FAQ

### Q1：批量任务遇到 429 怎么办？

先降低并发，按账号/模型维度设置令牌桶；对 429 做指数退避；不要让所有 worker 同时立即重试。高峰期可切到备用模型或更稳定分组。

### Q2：模型不可用或供应商短暂异常怎么办？

把模型调用封装成模型池：主模型失败后切到备用模型。业务上要记录 `request_id`、模型名、错误码、耗时和 token 估算，便于后续定位。

### Q3：上下文过长怎么办？

在业务服务层做分段摘要、压缩历史上下文、只保留必要字段。不要把整份数据库记录或全量聊天历史直接塞进 prompt。

### Q4：JSON 解析失败怎么办？

提示词要求“只返回 JSON”不等于一定成功。必须在代码里 `json.loads` / `JSON.parse`，失败后可用低温度重试一次，仍失败就进入人工检查或 fallback。

### Q5：成本异常怎么排查？

按任务类型、模型、分组、用户或客户 ID 记录消耗。常见原因包括：重复提交、无限重试、上下文过长、把低价值草稿误路由到高价模型。

## 9. 接入建议

- 先从非关键链路接入：例如内容草稿、摘要、标签生成；
- 对每个任务加幂等 key，避免重复扣费；
- 将失败任务落库，而不是阻塞整批；
- 每天复盘失败率、平均延迟和单条成本；
- 逐步把高价值任务迁移到更稳定分组。

联系方式：邮箱 <miutayoung@gmail.com>；Telegram `viral_8866`；WeChat `viral_8866`。如果你已经有真实调用量、能自助接入并希望用统一 OpenAI-compatible API 网关管理 Claude/GPT/Gemini 的内容生成流水线，可以从官网 <https://viralapi.ai> 和 GitHub 示例 <https://github.com/sxl7530-hashs/viralapi-examples> 开始。
