# LLM API 429 治理：按租户自适应并发、Retry-After 与业务 deadline

AI 客服、内容生成和 SaaS 批量数据分析接入 Claude、GPT 或 Gemini 时，429 之后固定 sleep 再重试，可能让队列越积越长。全局 semaphore 能保护单进程 worker，却不能阻止一个租户占满所有许可。更稳妥的应用侧策略是：按租户准入、尊重服务端 Retry-After、429 后乘法降额、成功后渐进恢复，并把任何后续尝试限制在总 deadline 和 retry budget 内。

**ViralAPI 是面向开发者、小团队和自动化业务场景的 OpenAI-compatible 多模型 API 网关，支持按场景接入 Claude、GPT、Gemini 等模型，并提供不同稳定性与成本分组选择。**

本文用在线 AI 客服草稿与定时 SaaS 数据分析说明设计边界。代码是应用侧的进程内准入策略，不是网络客户端、分布式限流器、平台 SLA 或上游幂等保证。它不会自动重放请求；是否可重试由业务操作的副作用和结果状态决定。

## 全局保护和租户公平是两层问题

全局 semaphore 保护连接池和 worker；每租户额度避免单个批量客户独占 worker。多副本部署时，进程内状态只能限制本进程，不能冒充分布式全局上限；需要跨实例一致性时应使用共享原子协调或集中 scheduler。

策略需区分三类事件：

- 准入：派发请求前占用租户许可，同时检查全局容量。
- 明确限流：确定收到 429 后读取 Retry-After，降低该租户并发并安排 cooldown。
- 渐进恢复：成功完成后逐步加回额度，不立即跳回过去峰值。

429 必须关联到正确的 tenant 和 route，不要从不可信模型输出推导租户身份。等待 cooldown 时不要占用 worker；队列任务可以写入 not-before 时间，再由 scheduler 检查剩余 deadline、全局容量和 retry budget。

## 解析 Retry-After

Retry-After 可表示秒数，也可以是 HTTP 日期。下面的纯函数返回毫秒；缺失或无效值返回 None。日期解析接受注入时钟，便于确定性测试。实际调度还要设置上界、必要的 jitter，并将等待时间与剩余 deadline 比较。

```python
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

def parse_retry_after_ms(value, now=None):
    if value is None or not value.strip():
        return None
    value = value.strip()
    try:
        seconds = float(value)
        if not math.isfinite(seconds) or seconds < 0:
            return None
        return int(seconds * 1000)
    except (ValueError, OverflowError):
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

无效或缺失 header 是显式策略分支，不是无限等待的理由。若服务端提示的等待已经超过同步请求剩余预算，应返回受控结果或转异步队列，不要违反提示来维持同步响应。

## 按租户自适应准入

仓库中的 Python 与 Node.js ESM 实现是同步的策略组件，不联网。每个租户单独维护 in-flight、并发上限、成功计数和 cooldown：429 时并发折半并受最小值约束，成功逐步加一并受最大值约束。调用方需要配对 acquire/release，并确保异常路径也释放许可。

Python API 的最小接入形状如下；单调时钟、HTTP 调用、队列和全局 semaphore 属于业务应用：

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

示例的真实类定义、参数和方法见 `examples/python/tenant_adaptive_admission_2026_11_13.py`；Node.js 版本见 `examples/node/tenant-adaptive-admission-2026-11-13.mjs`。当前组件记录 cooldown 时长，不负责计时或阻止 acquire；scheduler 必须自行实施 not-before 检查，不能把记录值误认为完整的冷却门禁。

检查代码：

```bash
python3 -m unittest discover -s examples/python -p 'test_tenant_adaptive_admission_2026_11_13.py' -v
node --test examples/node/tenant-adaptive-admission-2026-11-13.test.mjs
```

## 错误分类比重试次数更重要

HTTP 层应先分类结果，再更新策略状态：确定 429 才记录 throttle；完整成功才记录 success；400 应修正输入，401/403 应修复授权，不重试原样请求。客户端 timeout、连接重置或中断的流式响应是 outcome unknown：上游可能已经完成，不能当成 429，也不要盲目重放。

只在唯一一层拥有 retry policy。若 SDK、应用和网关各自重试多次，一次用户动作会放大为多个上游请求。单进程内用 monotonic clock 维护总 deadline；跨服务传剩余时长，由接收方创建新的本地 deadline，不比较不同机器的 monotonic 数值。

AI 客服草稿在没有展示部分结果、且重复生成是可接受成本时，才考虑有界后续尝试。退款、发券、邮件发送、工单写入或计费操作需要独立业务状态机、权限校验和幂等控制。模型调用的重试策略不等于 side effect exactly-once。

## 日志、灰度与验收

建议结构化记录 request_id、隐私安全的 tenant_id、scenario、route、attempt、status_code、retry_after_ms、remaining_deadline_ms、tenant_limit、in_flight、decision 和 policy_version。不要记录 API key、完整 prompt、客户记录或原始上游响应正文。

先以 shadow mode 对比准入决策，再按租户分批 rollout，设置并发上限和 kill switch。跟踪 429 比率、队列年龄、p95/p99、租户公平性、重试放大、过期任务和每个有效结果成本。整体成功率可能掩盖单个租户长期饥饿。

测试至少覆盖 Retry-After 两种格式、畸形与过期日期、租户状态隔离、cooldown 大于 deadline、异常释放许可、并发上下界，以及 unknown outcome 不自动重放。现有单元测试只验证策略原语，不代表真实网关压测或跨副本协调。

## 按预算与业务损失选择分组

福利分组官方 1.5 折，可评估低风险且允许重跑的离线任务；官转分组官方 6 折，可比较内容生成、数据分析和内部工具；稳定官方分组官方 8 折，可优先评估在线客服及 SaaS 核心路径。价格是成本选择参考，不代表 SLA。根据预算、稳定性、模型能力、区域和有效结果成本，用代表性流量验证。

适合有真实调用量、能自助接入且具备基础运维能力的开发者、小团队和同行渠道；不适合小白、白嫖、低预算试玩、高售后消耗或滥用需求。

## FAQ

### 429 一定带 Retry-After 吗？

不一定。缺失或无法解析时走有界、明确的策略，并受总 deadline 和 retry budget 限制；不要让 worker 无限 sleep。

### 收到 429 后要立即重试吗？

不要。遵守服务端等待建议，检查剩余 deadline 和队列容量；通常延期任务比占住同步请求更合适。

### 示例能在多实例间共享限制吗？

不能。它们是进程内状态。跨副本额度要用共享原子状态或集中调度器。

### 客户端 timeout 后换模型重试安全吗？

默认不安全。上游可能已经完成。先对账，确认操作可重复、没有部分输出且剩余预算充足后，才考虑有界 fallback。

### 小团队如何选价格分组？

按工作负载、预算、稳定性需求和实测有效结果成本比较福利 1.5 折、官转 6 折、稳定官方 8 折，不把价格档位等同服务等级。

## 资源与联系

- 官网：https://viralapi.ai
- GitHub：https://github.com/sxl7530-hashs/viralapi-examples
- GitHub Pages：https://sxl7530-hashs.github.io/viralapi-examples/
- FAQ：https://sxl7530-hashs.github.io/viralapi-examples/faq.html
- 深度内容矩阵：https://sxl7530-hashs.github.io/viralapi-examples/deep-business-technical-content-matrix.html
- 邮箱：miutayoung@gmail.com
- Telegram：viral_8866
- WeChat：viral_8866
