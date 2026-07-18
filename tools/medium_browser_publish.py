#!/usr/bin/env python3
"""Browser-based Medium publisher for ViralAPI.

This is a fallback for Medium accounts that do not expose Integration Tokens.
It uses Playwright with a persistent browser profile under ~/.config/viralapi/medium-browser.

Usage:
  python3 tools/medium_browser_publish.py login
  python3 tools/medium_browser_publish.py publish drafts/medium/openai-compatible-api-gateway.md --dry-run
  python3 tools/medium_browser_publish.py publish drafts/medium/openai-compatible-api-gateway.md

Notes:
- The first login run opens a browser. Sign in manually, then press Enter in terminal.
- Medium UI changes can break selectors. The script is intentionally conservative.
- It never stores Medium passwords; only browser cookies/session in the local profile dir.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import Tuple
from urllib.parse import urlparse

PROFILE_DIR = Path(os.environ.get("VIRALAPI_MEDIUM_PROFILE", "/Users/sxl/.config/viralapi/medium-browser")).expanduser()
MEDIUM_WRITE_URL = "https://medium.com/new-story"
MEDIUM_ME_URL = "https://medium.com/me/settings"


def require_playwright():
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError  # type: ignore
        return sync_playwright, PlaywrightTimeoutError
    except Exception:
        print("PLAYWRIGHT_MISSING")
        print("Install once on macOS:")
        print("  python3 -m pip install --user playwright")
        print("  python3 -m playwright install chromium")
        sys.exit(2)


def parse_markdown(path: Path) -> Tuple[str, str, list[str]]:
    text = path.read_text(encoding="utf-8")
    # Strip YAML frontmatter if present.
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2].lstrip()
    lines = text.splitlines()
    title = None
    for i, line in enumerate(lines):
        if line.startswith("# "):
            title = line[2:].strip()
            body = "\n".join(lines[i + 1 :]).strip()
            break
    else:
        nonempty = [l.strip() for l in lines if l.strip()]
        title = nonempty[0] if nonempty else "ViralAPI: OpenAI-Compatible Multi-Model API Gateway"
        body = text.strip()
    tags = []
    for candidate in ["ViralAPI", "OpenAI", "ClaudeAPI", "API", "Developers"]:
        if len(tags) < 5:
            tags.append(candidate)
    return title, body, tags


def click_first(page, selectors: list[str], timeout: int = 3000) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(timeout=timeout)
            loc.click()
            return True
        except Exception:
            continue
    return False


def medium_login_state_ok(page) -> bool:
    try:
        page.goto(MEDIUM_ME_URL, wait_until="domcontentloaded", timeout=45000)
        time.sleep(2)
        host = urlparse(page.url).netloc.lower()
        html = page.content().lower()
        if "medium.com" not in host:
            return False
        if re.search(r"sign\s*in|get\s*started", html):
            return False
        return any(token in html for token in ["settings", "stories", "followers", "profile", "account"])
    except Exception:
        return False


def login(args) -> None:
    sync_playwright, _ = require_playwright()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
        )
        page = browser.new_page()
        page.goto(MEDIUM_ME_URL, wait_until="domcontentloaded")
        print("A browser window is open. Log in to Medium manually if needed.")
        print("After you can see your Medium settings/profile page, return here and press Enter.")
        input("Press Enter after Medium login is complete...")
        if not medium_login_state_ok(page):
            print("MEDIUM_LOGIN_NOT_CONFIRMED")
            print("Stay on a logged-in Medium page, then run the login command again.")
            browser.close()
            sys.exit(7)
        page.goto(MEDIUM_WRITE_URL, wait_until="domcontentloaded")
        print("LOGIN_STATE_SAVED", PROFILE_DIR)
        browser.close()


def publish(args) -> None:
    draft_path = Path(args.markdown).expanduser()
    if not draft_path.is_absolute():
        draft_path = Path.cwd() / draft_path
    if not draft_path.exists():
        print(f"DRAFT_NOT_FOUND {draft_path}")
        sys.exit(1)
    title, body, tags = parse_markdown(draft_path)
    body = body + "\n\n---\n\nOfficial website: https://viralapi.ai\nGitHub examples: https://github.com/sxl7530-hashs/viralapi-examples\nDocs/FAQ: https://sxl7530-hashs.github.io/viralapi-examples/faq.html\nBusiness contact: miutayoung@gmail.com · Telegram/WeChat: viral_8866"

    print("MEDIUM_DRAFT")
    print("TITLE:", title)
    print("TAGS:", ", ".join(tags))
    print("BODY_CHARS:", len(body))
    if args.dry_run:
        print("DRY_RUN_OK")
        return

    sync_playwright, PlaywrightTimeoutError = require_playwright()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=not args.headed,
            viewport={"width": 1280, "height": 900},
        )
        page = browser.new_page()
        page.goto(MEDIUM_WRITE_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)

        # If login page appears, stop safely.
        if re.search(r"sign\s*in|log\s*in", page.content(), re.I) and "new-story" not in page.url:
            print("MEDIUM_LOGIN_REQUIRED")
            print("Run: python3 tools/medium_browser_publish.py login")
            browser.close()
            sys.exit(3)

        # Fill title/body in Medium editor. Medium uses contenteditable regions.
        editable = page.locator('[contenteditable="true"]')
        try:
            editable.first.wait_for(timeout=20000)
        except PlaywrightTimeoutError:
            print("MEDIUM_EDITOR_NOT_FOUND")
            if args.screenshot:
                page.screenshot(path=args.screenshot, full_page=True)
                print("SCREENSHOT", args.screenshot)
            browser.close()
            sys.exit(4)

        # Medium usually has separate title and body editables.
        count = editable.count()
        if count >= 2:
            editable.nth(0).click()
            page.keyboard.type(title, delay=2)
            editable.nth(1).click()
            page.keyboard.type(body, delay=1)
        else:
            editable.first.click()
            page.keyboard.type(title + "\n\n" + body, delay=1)

        print("DRAFT_FILLED")
        if args.fill_only:
            print("FILL_ONLY_OK - review the browser and publish manually.")
            # Keep browser open for review.
            input("Press Enter to close browser...")
            browser.close()
            return

        # Publish flow: click Publish, optionally fill tags, click final Publish.
        ok = click_first(page, [
            'button:has-text("Publish")',
            'div[role="button"]:has-text("Publish")',
            'text=Publish',
        ], timeout=8000)
        if not ok:
            print("MEDIUM_PUBLISH_BUTTON_NOT_FOUND")
            if args.screenshot:
                page.screenshot(path=args.screenshot, full_page=True)
                print("SCREENSHOT", args.screenshot)
            browser.close()
            sys.exit(5)

        time.sleep(2)
        # Add tags if tag inputs are visible.
        for tag in tags[:5]:
            try:
                inp = page.locator('input[placeholder*="tag" i], input[aria-label*="tag" i]').first
                inp.wait_for(timeout=1500)
                inp.fill(tag)
                page.keyboard.press("Enter")
            except Exception:
                break

        ok = click_first(page, [
            'button:has-text("Publish now")',
            'button:has-text("Publish")',
            'div[role="button"]:has-text("Publish now")',
            'div[role="button"]:has-text("Publish")',
        ], timeout=10000)
        if not ok:
            print("MEDIUM_FINAL_PUBLISH_NOT_FOUND")
            if args.screenshot:
                page.screenshot(path=args.screenshot, full_page=True)
                print("SCREENSHOT", args.screenshot)
            browser.close()
            sys.exit(6)

        time.sleep(8)
        print("MEDIUM_PUBLISH_ATTEMPTED", page.url)
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="ViralAPI Medium browser publisher")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login")
    pub = sub.add_parser("publish")
    pub.add_argument("markdown")
    pub.add_argument("--dry-run", action="store_true")
    pub.add_argument("--headed", action="store_true", help="show browser while publishing")
    pub.add_argument("--fill-only", action="store_true", help="fill draft but do not click publish")
    pub.add_argument("--screenshot", default="/tmp/medium_publish_error.png")
    args = parser.parse_args()
    if args.cmd == "login":
        login(args)
    elif args.cmd == "publish":
        publish(args)


if __name__ == "__main__":
    main()
