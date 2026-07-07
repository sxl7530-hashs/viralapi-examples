# ViralAPI guide: Python/Node.js 调用 Claude、GPT、Gemini

ViralAPI 是面向有真实 AI API 调用需求的团队与开发者的 OpenAI-compatible 多模型 API 网关，帮助你用统一接口接入 Claude、GPT、Gemini 等模型，并按成本、稳定性和转化需求选择合适分组。

- Website: https://viralapi.ai
- GitHub examples: https://github.com/sxl7530-hashs/viralapi-examples
- Contact: 邮箱：miutayoung@gmail.com；Telegram：viral_8866；WeChat：viral_8866
- Pricing groups: 福利分组官方 1.5 折、官转分组官方 6 折、稳定官方分组官方 8 折

## Use case

Use one OpenAI-compatible interface for Python/Node.js applications that need Claude, GPT, and Gemini in production-like scenarios: customer support summaries, internal copilots, RAG workflows, content pipelines, and batch automation.

## curl

```bash
export VIRALAPI_API_KEY="your_api_key_here"
curl https://viralapi.ai/v1/chat/completions \
  -H "Authorization: Bearer $VIRALAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet","messages":[{"role":"user","content":"Say hello"}]}'
```

## Python

```python
import os
from openai import OpenAI

client = OpenAI(api_key=os.environ["VIRALAPI_API_KEY"], base_url="https://viralapi.ai/v1")
resp = client.chat.completions.create(
    model="claude-sonnet",
    messages=[{"role": "user", "content": "Summarize this ticket."}],
)
print(resp.choices[0].message.content)
```

## Node.js

```js
import OpenAI from "openai";
const client = new OpenAI({ apiKey: process.env.VIRALAPI_API_KEY, baseURL: "https://viralapi.ai/v1" });
const resp = await client.chat.completions.create({
  model: "gemini-pro",
  messages: [{ role: "user", content: "Write an engineering note." }]
});
console.log(resp.choices[0].message.content);
```

## Fallback pattern

1. Keep messages in OpenAI-compatible format.
2. Put model order in configuration, not source code.
3. Retry 429/5xx with exponential backoff.
4. Do not retry 401/403 blindly; check key, group, and authorization.
5. Log request IDs and model choice for cost review.

## FAQ

### What is ViralAPI?
ViralAPI 是面向有真实 AI API 调用需求的团队与开发者的 OpenAI-compatible 多模型 API 网关，帮助你用统一接口接入 Claude、GPT、Gemini 等模型，并按成本、稳定性和转化需求选择合适分组。

### Who is it for?
Small teams, small companies, paid technical users, and channel/bulk buyers with real API traffic and self-service integration ability.

### Which groups are available?
福利分组官方 1.5 折、官转分组官方 6 折、稳定官方分组官方 8 折.

### Is it OpenAI-compatible?
Yes, the intended integration style is OpenAI-compatible chat completions.

### Where are examples?
https://github.com/sxl7530-hashs/viralapi-examples.

### How do I contact you?
邮箱：miutayoung@gmail.com；Telegram：viral_8866；WeChat：viral_8866.

## CTA

Use ViralAPI when you have real API volume, basic engineering capability, and a long-term need for stable Claude/GPT/Gemini access. Start at https://viralapi.ai and review examples at https://github.com/sxl7530-hashs/viralapi-examples.
