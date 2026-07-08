# ViralAPI Examples

OpenAI-compatible API examples for developers and small teams using **ViralAPI**.

ViralAPI helps developers, small teams, automation workflows, and API-heavy users access multiple LLM providers through an OpenAI-compatible interface. Typical use cases include Claude, GPT, Gemini, model fallback, cost-aware routing, and unified integration for production workloads.

- Website: <https://viralapi.ai>
- Documentation: <https://rcnh0cyauux7.feishu.cn/wiki/OskTwo9agi0g9tkZF6Gcxvqun6m>
- GitHub Pages docs: <https://sxl7530-hashs.github.io/viralapi-examples/>
- Business email: <miutayoung@gmail.com>
- Telegram: `viral_8866`
- WeChat: `viral_8866`

> This repository is intended for users with basic technical ability who can integrate APIs independently. It is not a no-code or beginner-only tutorial.

## Pricing groups

ViralAPI provides different groups for different usage priorities:

| Group | Positioning | Reference pricing |
| --- | --- | --- |
| Welfare group | Cost-sensitive testing and light workloads | Around **15% of official pricing** / 福利分组约官方 **1.5折** |
| Official-transfer group | Balance between cost and official-source routing | Around **60% of official pricing** / 官转分组约官方 **6折** |
| Stable official group | More stability-oriented production usage | Around **80% of official pricing** / 稳定官方分组约官方 **8折** |

Choose the group based on your budget, stability requirements, and expected request volume.

## Quick start

These examples assume an OpenAI-compatible API interface.

Set environment variables first:

```bash
export VIRALAPI_API_KEY="your_api_key_here"
export VIRALAPI_BASE_URL="https://your-viralapi-openai-compatible-endpoint/v1"
```

You can find the correct endpoint and API key in the ViralAPI website/dashboard or documentation.

## Examples

- [curl example](examples/curl/chat-completions.sh)
- [Python example](examples/python/chat_completions.py)
- [Node.js example](examples/node/chat-completions.mjs)

## curl

```bash
bash examples/curl/chat-completions.sh
```

## Python

```bash
python3 examples/python/chat_completions.py
```

## Node.js

```bash
node examples/node/chat-completions.mjs
```

## Integration notes

### Why use an OpenAI-compatible gateway?

For small teams, calling each model provider directly can create extra maintenance work:

- different SDKs and API formats;
- separate billing and usage tracking;
- model fallback and switching logic;
- unstable availability across regions or providers;
- harder cost control when workloads grow.

An OpenAI-compatible API gateway lets you keep one integration layer while switching models or groups behind the scenes.

### Suitable users

ViralAPI is more suitable for:

- developers and small teams with real API usage;
- AI product builders who need Claude/GPT/Gemini style model access;
- automation teams with recurring token consumption;
- users who understand API keys, request payloads, and basic troubleshooting;
- reseller/channel users with stable volume needs.

### Not ideal for

ViralAPI is not the best fit if you only want:

- a free unlimited trial;
- one-time casual chatbot usage;
- no-code help without basic API knowledge;
- high-risk or abusive workloads;
- support-heavy usage without the ability to self-debug basic integration issues.

## FAQ

### Is ViralAPI compatible with OpenAI SDKs?

ViralAPI is designed around OpenAI-compatible API usage. In most integrations, you set a custom `base_url` and API key, then keep the rest of your OpenAI-style request code similar.

### Can I use Claude, GPT, and Gemini through one integration?

That is the intended use case: one OpenAI-compatible integration layer, with different model or group choices depending on your account configuration.

### Which pricing group should I choose?

- Choose the welfare group if cost sensitivity is the main factor.
- Choose the official-transfer group if you want a balance between cost and official-source routing.
- Choose the stable official group if production stability matters more than the lowest cost.

### How do I contact ViralAPI for business cooperation?

Email: <miutayoung@gmail.com>  
Telegram: `viral_8866`  
WeChat: `viral_8866`

## License

MIT
