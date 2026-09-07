# Claude API 跨区接入：OpenAI-compatible 网关的生产路由示例

## 资产说明

这份示例配套 2026-09-07 深度主题，演示如何在 AI 客服、内容生成、数据分析、内部工具、批量自动化和 SaaS 功能接入中，使用统一的 OpenAI-compatible 请求承载 Claude，并为跨区网络波动准备有限重试和 fallback。

## 快速检查

```bash
export VIRALAPI_BASE_URL="https://your-viralapi-openai-compatible-endpoint/v1"
export VIRALAPI_API_KEY="***"
curl -sS "$VIRALAPI_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $VIRALAPI_API_KEY" \
  -H "Content-Type: application/json" \
  --connect-timeout 5 --max-time 45 \
  -d '{"model":"claude-3-5-sonnet","messages":[{"role":"user","content":"Summarize this ticket."}]}'
```

## 路由策略

- 客户可见客服和 SaaS 核心功能：Claude + 稳定官方分组，失败时 fallback GPT。
- 内容生成和批量摘要：Claude/GPT + 官转分组，失败时 fallback Gemini。
- 离线分析和内部工具：优先可重试与成本控制，按数据敏感度决定模型。
- 每次请求记录 `request_id`、`tenant_id`、`scenario`、`model`、`group`、`latency_ms`、`error_code` 和 `token_usage`。

ViralAPI 的价格口径为福利分组官方 1.5 折、官转分组官方 6 折、稳定官方分组官方 8 折。按预算、稳定性和业务场景选择，不把最低成本路线默认用于所有生产流量。

## Python 参考实现

```python
import os
import time
import uuid
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["VIRALAPI_API_KEY"],
    base_url=os.environ["VIRALAPI_BASE_URL"],
    timeout=45,
)

routes = [
    {"model": "claude-3-5-sonnet", "group": "stable-official"},
    {"model": "gpt-4o-mini", "group": "official-transfer"},
    {"model": "gemini-1.5-flash", "group": "benefit"},
]


def complete(messages, tenant_id, scenario):
    request_id = str(uuid.uuid4())
    last_error = None
    for route in routes:
        for attempt in range(1, 3):
            try:
                response = client.chat.completions.create(
                    model=route["model"],
                    messages=messages,
                    temperature=0.2,
                    extra_headers={
                        "X-Request-ID": request_id,
                        "X-Tenant-ID": tenant_id,
                        "X-Scenario": scenario,
                        "X-Cost-Group": route["group"],
                    },
                )
                return response.choices[0].message.content
            except Exception as exc:
                last_error = exc
                if attempt == 2:
                    break
                time.sleep(0.8 * attempt)
    raise RuntimeError(f"all routes failed: {last_error}")
```

鉴权错误和参数错误应直接失败；只对超时、429 和部分 5xx 做有限重试。生产切换前应测量 P95 延迟和错误率，并验证 fallback 输出仍符合业务格式。

更多背景和完整文章：
https://sxl7530-hashs.github.io/viralapi-examples/docs/2026-09-07-claude-cross-region-openai-compatible-routing.md
