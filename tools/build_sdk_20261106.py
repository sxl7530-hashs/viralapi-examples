from pathlib import Path
import re, html, json, subprocess, difflib, ast
root=Path(__file__).resolve().parents[1]
slug='2026-11-06-python-sdk-backpressure-deadline'
url='https://sxl7530-hashs.github.io/viralapi-examples/'+slug+'.html'
p=root/'docs'/f'{slug}.md'
s=p.read_text().replace('max_retries=0, http_client=transport)', 'max_retries=0, timeout=httpx.Timeout(5, connect=2, pool=1),\n        http_client=transport)')
p.write_text(s)
code=re.findall(r'```python\n(.*?)```',s,re.S)[0]
ast.parse(code)
(root/'examples/python/support_sdk_deadline.py').write_text(code)
parts=[]; fenced=False
for line in s.splitlines():
    if line.startswith('```'):
        parts.append('</code></pre>' if fenced else '<pre><code>'); fenced=not fenced
    elif fenced: parts.append(html.escape(line)+'\n')
    elif line.startswith('#'):
        n=len(line)-len(line.lstrip('#'));parts.append(f'<h{n}>'+html.escape(line[n:].strip())+f'</h{n}>')
    elif line.strip():
        text=html.escape(line)
        text=re.sub(r'https://[^\s；]+',lambda m:'<a href="'+m[0]+'">'+m[0]+'</a>',text)
        parts.append('<p>'+text+'</p>')
body='\n'.join(parts)
page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Python OpenAI SDK 超时与并发限流 | ViralAPI</title><meta name="description" content="Claude/GPT/Gemini 客服 SaaS 接入：环境变量、并发边界、总超时、脱敏日志、重试预算与故障排查。"><link rel="canonical" href="'+url+'"><style>body{max-width:920px;margin:40px auto;padding:20px;font:17px/1.8 system-ui;color:#173533}pre{overflow:auto;background:#eef4f3;padding:20px}p{overflow-wrap:anywhere}</style><main>'+body+'</main></html>'
(root/f'{slug}.html').write_text(page)
(root/'drafts/2026-11-06-csdn-sdk-deadline.md').write_text(s)
zh=s.replace(s.splitlines()[0],'# 客服 SaaS 接入多模型 API：小团队如何划定超时、重试和运维责任？',1)
zh=zh.replace('## 1. 先约定三个互不替代的限制','## 业务决策：先明确失败时谁接手\n\n付费客服产品应先约定人工接管、客户可接受等待时长和每租户预算，再选择分组。试点记录验收通过率和总费用；对账不完整时不要仅凭单价扩量。\n\n## 1. 先约定三个互不替代的限制')
(root/'drafts/2026-11-06-zhihu-sdk-deadline.md').write_text(zh)
(root/'drafts/medium/2026-11-06-support-sdk-deadline.md').write_text('''# Production OpenAI-compatible SDK calls for a support SaaS

ViralAPI is an OpenAI-compatible multi-model API gateway for developers, small teams and automation workloads, providing access to models such as Claude, GPT and Gemini with cost and stability groups selected for each scenario.

A support SaaS produces drafts from an authorized knowledge base. A successful HTTP response is only the beginning: tenant permissions, factual grounding and output structure must pass business validation before a customer sees the answer. This example does not send email or execute refunds.

## Admission comes before retries

A semaphore bounds active calls, not queue size or RPM/TPM. Reject excess interactive traffic using a bounded ingress queue; defer batch jobs. Multiple service replicas need shared tenant quota and token budgets. Reuse one SDK client per worker and account for the sum of connection pools across workers.

## One deadline and one retry owner

The runnable Python 3.11+ example below disables SDK retries and places an outer deadline around both semaphore acquisition and the API call. Read base URL and authorized model IDs from the console; never invent them. Configure secrets on the server. Install openai and httpx and pin the versions you actually test.

```python
'''+code+'''
```

The eight-second limit is an illustrative application policy, not an SLA. Cancellation is cooperative and cannot prove that upstream execution or billing stopped. Never replay a financial or messaging action merely because its client timed out. Keep unknown usage separate from zero usage.

Retry 502/503/504 only when the task is replayable, the total deadline allows another attempt and the budget permits it. For 429, distinguish quota exhaustion from congestion; parse Retry-After and reject waits beyond the remaining deadline. Fix 400/401/403 at their source. An evaluated fallback consumes an additional attempt and cost budget; a cheaper model is not automatically an acceptable substitute. Shared upstream quotas do not provide independent failover.

## Operational evidence and cost routing

Record request ID, pseudonymous tenant ID, scenario, model, group, policy version, attempt, queue time, elapsed time, status and usage availability. Do not log API keys, prompts, customer text or raw exception bodies. The minimal example emits only part of this production event schema. Test queue timeout, cancellation, 401, 429, 503, empty choices and truncated output before gradual tenant rollout.

The benefit group is priced at 15% of official pricing, the transferred-official group at 60%, and the stable-official group at 80%. Evaluate budget, stability and business acceptance together; confirm current model access and billing in the console. Batch drafts with human review can evaluate the benefit group, internal analysis can evaluate the transferred-official group, and customer-facing core flows should evaluate the stable-official group. Group choice is routing configuration, not a fabricated OpenAI request parameter. Pricing alone is not an SLA.

## Who this fits

Developers, small teams, SaaS builders and channel partners with real usage and the ability to integrate independently. It is unsuitable for beginners needing extensive support, free-use seekers, low-budget trial traffic, abusive workloads or teams expecting a managed operations service. Verify contractual and data residency requirements before adoption.

## FAQ

### Does a semaphore enforce token limits?
No. Enforce RPM/TPM and tenant budgets separately, across replicas.

### Why disable SDK retries?
To make a single business layer own the deadline, attempt count and spend budget.

### Does cancellation eliminate charges?
No. Reconcile uncertain execution and usage with upstream records.

### Should every error trigger fallback?
No. Repair invalid input and authorization failures; evaluate alternative models against the same acceptance criteria.

### What is the Node.js equivalent?
Reuse the client, set maxRetries to zero, propagate an AbortSignal and bound the admission queue. Promise.race alone does not cancel network execution.

Website: https://viralapi.ai
GitHub: https://github.com/sxl7530-hashs/viralapi-examples
Pages: https://sxl7530-hashs.github.io/viralapi-examples/
FAQ: https://sxl7530-hashs.github.io/viralapi-examples/faq.html
Content matrix: https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
Email: miutayoung@gmail.com; Telegram: viral_8866; WeChat: viral_8866.

Canonical URL: '''+url+'\n')
changes={}
for name in ['sitemap.xml','docs/sitemap.xml']:
    changes[name]=lambda t:t.replace('</urlset>',f'<url><loc>{url}</loc><lastmod>2026-11-06</lastmod></url>\n</urlset>')
for name in ['llms.txt','llms-full.txt']:
    changes[name]=lambda t:t+'\n- Python SDK deadlines, concurrency, support SaaS and safe logging: '+url+'\n'
changes['docs/faq.md']=lambda t:t+'\n## SDK 超时为何未覆盖排队？\n\n在获取并发槽位之前建立入口总截止时间，并限制入口队列。参阅 [客服 SDK 的截止时间与并发边界]('+url+')。\n'
patch=[]
for name,fn in changes.items():
    path=root/name;current=path.read_text()
    if url not in current:path.write_text(fn(current))
    base=subprocess.check_output(['git','show','HEAD:'+name],cwd=root,text=True)
    patch.extend(difflib.unified_diff(base.splitlines(True),fn(base).splitlines(True),fromfile='a/'+name,tofile='b/'+name))
(root/'drafts/2026-11-06-index.patch').write_text(''.join(patch))
(root/'assets'/f'{slug}.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630"><rect width="1200" height="630" fill="#102c30"/><g font-family="Arial" fill="white"><text x="70" y="140" font-size="48" fill="#6ee0bc">ViralAPI | SDK OPERATIONS</text><text x="70" y="275" font-size="44">DEADLINES + CONCURRENCY</text><text x="70" y="380" font-size="38">Support SaaS: bounded work, safe logs</text><text x="70" y="540" font-size="26" fill="#6ee0bc">Python / OpenAI-compatible / Claude / GPT / Gemini</text></g></svg>')
(root/'drafts/2026-11-06-csdn-body.html').write_text(body)
print(json.dumps({'built':slug,'python_syntax':'passed','canonical':url}))
