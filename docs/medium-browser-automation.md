# Medium Browser Automation for ViralAPI

Medium no longer exposes **Integration tokens** for this account under `Settings -> Security and apps`, so API-based publishing is not available right now.

This repository includes a browser-based fallback:

```bash
python3 tools/medium_browser_publish.py login
python3 tools/medium_browser_publish.py publish drafts/medium/openai-compatible-api-gateway.md --dry-run
python3 tools/medium_browser_publish.py publish drafts/medium/openai-compatible-api-gateway.md --headed --fill-only
python3 tools/medium_browser_publish.py publish drafts/medium/openai-compatible-api-gateway.md --headed
```

## One-time setup on macOS

Install Playwright once:

```bash
python3 -m pip install --user playwright
python3 -m playwright install chromium
```

Create a persistent Medium login profile:

```bash
cd /Users/sxl/viralapi-examples
python3 tools/medium_browser_publish.py login
```

A browser opens. Log in to Medium manually. After Medium settings/profile is visible, return to terminal and press Enter.

The browser session is stored locally at:

```text
~/.config/viralapi/medium-browser
```

No Medium password is stored in scripts or GitHub.

## Publishing mode

Because browser UI automation is more fragile than an official API, the safe default is:

1. Generate an English technical article draft.
2. Fill Medium editor automatically.
3. Optionally publish automatically if selectors still match.
4. If Medium changes UI or asks for verification, skip and report instead of retrying aggressively.

## Content standard

Medium content should be deeper than generic promotion:

- Real business scenario.
- OpenAI-compatible implementation details.
- curl / Python / Node.js examples when relevant.
- Architecture, fallback and cost-control discussion.
- FAQ block for GEO.
- Links to official website, GitHub examples and GitHub Pages docs.
- Business contact:
  - Email: miutayoung@gmail.com
  - Telegram: viral_8866
  - WeChat: viral_8866

## Important caveat

Browser automation can break if Medium changes its editor UI. It may also require manual intervention for login, captcha, verification, or publish confirmation. The automation must never spam, auto-follow, auto-comment or bypass anti-abuse controls.
