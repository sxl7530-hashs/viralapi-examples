# Medium Automation for ViralAPI

This repository includes a small Medium publishing script:

```bash
python3 tools/medium_publish.py \
  --title "What Is an OpenAI-Compatible Multi-Model API Gateway?" \
  --content-file drafts/medium/openai-compatible-api-gateway.md \
  --tags "AI,API,Claude,OpenAI,Developers" \
  --status public
```

## One-time setup

Medium publishing requires an Integration Token from the Medium account. Do not paste it into chat.

1. Open Medium settings:
   `https://medium.com/me/settings`
2. Find **Integration tokens**.
3. Generate a token, e.g. `viralapi-auto-publishing`.
4. Save it locally on the Mac:

```bash
mkdir -p ~/.config/viralapi
nano ~/.config/viralapi/medium_token
chmod 600 ~/.config/viralapi/medium_token
```

Paste only the token into that file and save.

## Safety rules

- The script never prints the token.
- The token is read from `MEDIUM_TOKEN` or `~/.config/viralapi/medium_token`.
- If no token exists, the daily job should skip Medium and report `MEDIUM_TOKEN_MISSING`.
- Medium should publish 2-3 higher-quality English posts per week, not low-quality daily spam.

## ViralAPI content requirements

Each Medium article should include:

- ViralAPI definition in the first section.
- A practical developer/business scenario.
- Architecture, code, or implementation details.
- Links to:
  - https://viralapi.ai
  - https://github.com/sxl7530-hashs/viralapi-examples
  - https://sxl7530-hashs.github.io/viralapi-examples/
- Pricing groups:
  - 福利分组: official price × 1.5折
  - 官转分组: official price × 6折
  - 稳定官方分组: official price × 8折
- Business contact:
  - Email: miutayoung@gmail.com
  - Telegram: viral_8866
  - WeChat: viral_8866

