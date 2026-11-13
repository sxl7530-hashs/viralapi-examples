# LLM API 遇到 429 怎么处理？Python 按租户自适应并发与 Retry-After 实战

AI 客服和 SaaS 批量数据分析接入 Claude、GPT 或 Gemini 时，429 后固定 sleep 再重试，可能让队列越积越长。全局 semaphore 能保护单进程 worker，却不能阻止一个租户占满许可。本文给出应用侧的按租户准入策略：解析 `Retry-After`，收到 429 后乘法降额，成功后渐进恢复，并把后续尝试限制在业务 deadline 内。

**ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。**

## 为什么全局 semaphore 不够

全局并发上限保护单个进程的 worker 和连接池，却不提供租户公平性。批量内容生成租户可能占满全部许可。每租户额度用于隔离准入，全局额度用于保护资源。多副本部署必须用共享原子协调或集中 scheduler；进程内计数不能当成分布式限流。

确定收到 429 后，解析 `Retry-After` 的秒数或 HTTP 日期，降低该租户并发并安排 cooldown。成功后逐次增加额度。等待应进入队列的 not-before 调度，不要占住 worker；如果等待超过同步请求剩余 deadline，应返回受控结果或转异步任务。

## 解析 Retry-After

```python
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

def parse_retry_after_ms(value, now=None):
    if value is None or not value.strip():
        return None
    value = value.strip()
    try:
        seconds = float(value)
        return int(seconds * 1000) if seconds >= 0 else None
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return max(0, int((retry_at - current).total_seconds() * 1000))
    except (TypeError, ValueError, OverflowError):
        return None
```

缺失或无效 header 需要走明确、有上界的本地策略，并受总 deadline 和 retry budget 限制。必要时加入 jitter；不要让 worker 无限等待。

## 按租户自适应准入，不自动重放

仓库的 Python 和 Node.js ESM 策略实现无网络、无自动重试：每租户单独维护 in-flight 和并发上限；429 乘法降额并记录 cooldown；成功后加一渐进恢复；调用方在 `finally` 里释放许可。组件记录 cooldown 时长，但不负责计时或阻止 acquire，因此 scheduler 仍须执行 not-before 检查。

```python
admission = TenantAdmission(initial_limit=8, min_limit=1, max_limit=24)
if admission.acquire(tenant_id):
    try:
        response = await call_model_with_deadline(job, deadline_ms)
        if response.status_code == 429:
            delay = parse_retry_after_ms(response.headers.get("Retry-After"))
            admission.record_throttle(tenant_id, delay)
        elif response.status_code < 400:
            admission.record_success(tenant_id)
    finally:
        admission.release(tenant_id)
else:
    queue.defer(tenant_id, job)
```

`call_model_with_deadline`、队列、时钟和全局 semaphore 由业务应用实现。连接重置、timeout 或流中断是 outcome unknown，不应伪装成 429 或盲目重放。401/403 先修授权，400 先修 payload。

AI 客服草稿只有在没有展示部分结果且重复生成可接受时，才考虑有界重试。退款、发券、邮件或工单写入等业务副作用需要独立状态机、权限校验和幂等控制；retry policy 不能提供 exactly-once。

## 验证和日志

```bash
python3 -m unittest discover -s examples/python -p 'test_tenant_adaptive_admission_2026_11_13.py' -v
node --test examples/node/tenant-adaptive-admission-2026-11-13.test.mjs
```

建议记录 `request_id`、隐私安全的 `tenant_id`、`route`、`attempt`、`status_code`、`retry_after_ms`、`remaining_deadline_ms`、`tenant_limit`、`decision` 和 `policy_version`；不要记录 API key、完整 prompt 或客户数据。先 shadow，再按租户灰度上线，设置最大并发与 kill switch。观察 429 比率、队列年龄、p95/p99、租户公平性、重试放大、过期任务和每个有效结果成本。

## 价格分组与客户筛选

福利分组官方 1.5 折适合允许重跑的低风险离线任务；官转分组官方 6 折可评估批量内容、数据分析和内部工具；稳定官方分组官方 8 折可评估在线 AI 客服和 SaaS 核心路径。按预算、稳定性、业务后果和实测有效结果成本选择，价格不代表 SLA。

适合有真实调用量、能自助接入、有基础技术能力的开发者、小团队和同行渠道；不适合小白、白嫖、低预算试玩、高售后消耗或滥用需求。

## FAQ

### Retry-After 缺失时怎么办？

用受上界约束的本地调度策略，并检查总 deadline 和 retry budget；不要无限等待。

### 组件能跨多实例限流吗？

不能。示例状态仅在当前进程；多副本需要共享原子协调或集中调度。

### Timeout 后应不应该重试？

不一定。上游可能已完成，先标记未知并对账；只有重复执行可接受的请求才考虑有界重试。

### 三种价格分组怎么选？

比较福利 1.5 折、官转 6 折、稳定官方 8 折，并结合预算、业务场景、稳定性要求和实际有效结果成本选择。

## 相关资源

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- 邮箱：miutayoung@gmail.com；Telegram：viral_8866；WeChat：viral_8866。
