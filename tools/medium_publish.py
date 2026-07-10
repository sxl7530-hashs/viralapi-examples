#!/usr/bin/env python3
"""Publish a Medium story for ViralAPI.

Secret safety:
- Reads token from MEDIUM_TOKEN or ~/.config/viralapi/medium_token
- Never prints token
- Intended for automated Hermes cron runs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

TOKEN_PATH = Path.home() / ".config" / "viralapi" / "medium_token"
API_BASE = "https://api.medium.com/v1"


def read_token() -> str:
    token = os.environ.get("MEDIUM_TOKEN", "").strip()
    if token:
        return token
    try:
        token = TOKEN_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = ""
    return token


def request(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    body = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API_BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Medium API HTTP {e.code}: {detail}") from e


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish ViralAPI article to Medium")
    parser.add_argument("--title", required=True)
    parser.add_argument("--content-file", required=True)
    parser.add_argument("--tags", default="AI,API,Claude,OpenAI,Developers")
    parser.add_argument("--status", default="public", choices=["public", "draft", "unlisted"])
    parser.add_argument("--canonical-url", default="")
    args = parser.parse_args()

    token = read_token()
    if not token:
        print(
            "MEDIUM_TOKEN_MISSING: create a Medium integration token and save it to "
            "~/.config/viralapi/medium_token (chmod 600).",
            file=sys.stderr,
        )
        return 2

    content_path = Path(args.content_file)
    content = content_path.read_text(encoding="utf-8")
    me = request("GET", "/me", token)
    user_id = me["data"]["id"]
    payload = {
        "title": args.title,
        "contentFormat": "markdown",
        "content": content,
        "tags": [t.strip() for t in args.tags.split(",") if t.strip()][:5],
        "publishStatus": args.status,
        "notifyFollowers": False,
    }
    if args.canonical_url:
        payload["canonicalUrl"] = args.canonical_url
    result = request("POST", f"/users/{user_id}/posts", token, payload)
    data = result.get("data", {})
    print(json.dumps({"id": data.get("id"), "url": data.get("url"), "title": data.get("title")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
