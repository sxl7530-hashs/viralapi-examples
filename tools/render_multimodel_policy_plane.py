#!/usr/bin/env python3
"""Render a responsive standalone page from the canonical 2026-11-03 guide."""
from html import escape
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / "docs/2026-11-03-small-team-multimodel-policy-plane.md"
output = root / "2026-11-03-small-team-multimodel-policy-plane.html"
lines = source.read_text(encoding="utf-8").splitlines()
body = []
in_code = False
code = []
for line in lines:
    if line.startswith("---"):
        continue
    if line.startswith("```"):
        if in_code:
            body.append("<pre><code>" + escape("\n".join(code)) + "</code></pre>")
            code = []
            in_code = False
        else:
            in_code = True
        continue
    if in_code:
        code.append(line)
    elif line.startswith("# "):
        body.append("<h1>" + escape(line[2:]) + "</h1>")
    elif line.startswith("## "):
        body.append("<h2>" + escape(line[3:]) + "</h2>")
    elif line.startswith("### "):
        body.append("<h3>" + escape(line[4:]) + "</h3>")
    elif line.startswith("| "):
        body.append("<p>" + escape(line) + "</p>")
    elif line.startswith("- "):
        body.append("<p>• " + escape(line[2:]) + "</p>")
    elif line.strip():
        body.append("<p>" + escape(line.replace("**", "")) + "</p>")

page = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>小团队 Claude/GPT/Gemini 统一网关架构：策略平面与能力路由 | ViralAPI</title>
<meta name="description" content="小团队多模型 API 网关实战：用 OpenAI-compatible 数据平面、版本化策略、能力矩阵、总 deadline、有限 fallback 和审计日志统一调用 Claude、GPT、Gemini。">
<link rel="canonical" href="https://sxl7530-hashs.github.io/viralapi-examples/2026-11-03-small-team-multimodel-policy-plane.html">
<style>body{font:17px/1.75 system-ui,sans-serif;color:#17252b;margin:0;background:#f4f7f7}main{max-width:900px;margin:0 auto;padding:32px 24px;background:white}h1{font-size:34px;line-height:1.2}h2{font-size:24px;margin-top:36px}p{overflow-wrap:anywhere}pre{overflow:auto;background:#17252b;color:#e8f1ee;padding:18px;border-radius:6px;font-size:14px}a{color:#086b64}@media(max-width:600px){main{padding:22px 16px}h1{font-size:28px}}</style>
</head><body><main>""" + "\n".join(body) + "</main></body></html>\n"
output.write_text(page, encoding="utf-8")
print(output)
