# 05 — Hook 系统

**对应代码**：`app/pipeline/core/hook.py`

## 🎯 读完本文你能回答

- Hook 解决了什么问题？
- `Phase` 枚举的 5 个值分别在什么时机触发？
- `SKIP_STAGE` 和 `ABORT` 有什么区别？
- Phase 3 会实现哪 4 个 Hook？

---

## Hook 解决的问题

有一些能力是每个 Stage 都需要的，比如：

- 记录每个 Stage 的耗时（可观测性）
- 检查配额（不能超过 API 调用次数）
- 验证租户边界（不能访问别人的数据）
- 防止重复处理（幂等性）

**不用 Hook 的写法**：

```python
async def _execute(self, ctx, input):
    if not ctx.quota.has_api_quota():       # 每个 Stage 都要写
        raise QuotaExceededError()
    start = time.time()
    result = await self._do_work(input)
    metrics.record(self.name, time.time() - start)   # 每个 Stage 都要写
    return result
```

每个 Stage 都塞了重复的横切代码，且 Stage 业务逻辑和基础设施能力混在一起。

**用 Hook 的写法**：

```python
async def _execute(self, ctx, input):
    return await self._do_work(input)   # Stage 只写业务逻辑
```

配额检查、计时、日志全部由 Hook 在 Stage 外部注入，Stage 代码零感知。

---

## Phase 枚举

```python
class Phase(Enum):
    PRE_PIPELINE  = "pre_pipeline"   # 整条 Pipeline 开始前（一次）
    PRE_STAGE     = "pre_stage"      # 每个 Stage 执行前
    POST_STAGE    = "post_stage"     # 每个 Stage 执行后（成功）
    POST_PIPELINE = "post_pipeline"  # 整条 Pipeline 结束后（一次）
    ON_ERROR      = "on_error"       # 任意 Stage 抛异常时
```

触发时机图：

```
PRE_PIPELINE
    │
    ├─ PRE_STAGE  [Stage 1]  POST_STAGE
    ├─ PRE_STAGE  [Stage 2]  POST_STAGE
    │                 └── 如果异常 → ON_ERROR
    └─ PRE_STAGE  [Stage 3]  POST_STAGE
                              │
                         POST_PIPELINE
```

---

## HookResult

```python
class HookResult(Enum):
    CONTINUE   = "continue"    # 继续正常执行
    SKIP_STAGE = "skip_stage"  # 跳过当前这个 Stage（但继续下一个）
    ABORT      = "abort"       # 终止整条 Pipeline，抛 PipelineAbortedError
```

| 结果 | 使用场景 | 谁用 |
|------|---------|-----|
| `CONTINUE` | 正常情况，大部分 Hook 返回这个 | 所有 Hook |
| `SKIP_STAGE` | 幂等：这个 Stage 已处理过，跳过 | `IdempotencyGuard` |
| `ABORT` | 配额超限，拒绝继续处理 | `QuotaGuard` |

---

## BaseHook 定义

```python
class BaseHook(ABC):
    phase: ClassVar[Phase | list[Phase]]   # 声明自己处理哪个（些）Phase
    priority: ClassVar[int] = 100          # 数字越小越先执行

    @abstractmethod
    async def handle(self, event: HookEvent) -> HookResult: ...
```

`phase` 可以是单个 Phase 或列表：

```python
class ObservabilityHook(BaseHook):
    phase = [Phase.PRE_STAGE, Phase.POST_STAGE, Phase.ON_ERROR]
    # 同时处理三个时机
```

---

## HookRunner：执行器

```python
class HookRunner:
    def __init__(self, hooks: list[BaseHook]):
        self.hooks = sorted(hooks, key=lambda h: h.priority)
        # 按优先级排序，priority 小的先执行

    async def fire(self, phase, ctx, stage=None, error=None):
        event = HookEvent(phase=phase, ctx=ctx, stage=stage, error=error)
        for hook in self.hooks:
            if phase not in hook.phases:
                continue          # 跳过不监听此 Phase 的 Hook
            result = await hook.handle(event)
            if result == HookResult.ABORT:
                raise PipelineAbortedError(...)
            if result == HookResult.SKIP_STAGE:
                return HookResult.SKIP_STAGE
        return HookResult.CONTINUE
```

---

## Phase 3 实现的 4 个 Hook

### TenantGuard（priority=10，最先）

```
Phase: PRE_PIPELINE
职责：正则校验 tenant_id 格式（3-64位字母数字连字符，首尾不能是连字符）
失败：raise ValueError（不是 ABORT，格式错误属于调用方 bug）
```

Phase 4 可升级为：查租户注册表，验证租户状态（active / suspended）。

### QuotaGuard（priority=20）

```
Phase: PRE_PIPELINE
职责：检查 ctx.quota.has_api_quota() 和 ctx.quota.has_storage_quota()
失败：raise QuotaExceededError（不是 ABORT）
```

> **注意**：实际实现只有 PRE_PIPELINE，没有 POST 扣减。配额快照在请求开始时固化在 Context，本期不写入数据库。

### IdempotencyGuard（priority=30）

```
Phase: PRE_STAGE + POST_STAGE（同一个 Hook 处理两个时机）
Key 格式：idempotency:{task_id}:{stage_name}

PRE_STAGE：redis.exists(key) → 存在则返回 SKIP_STAGE
POST_STAGE：redis.setex(key, 86400, "1") → 打完成标记，TTL 24h
```

崩溃重试时，已完成的 Stage 会被跳过，不会重复调用 OpenAI API（省钱）。

### ObservabilityHook（priority=100，最后）

```
Phase: PRE_PIPELINE / PRE_STAGE / POST_STAGE / POST_PIPELINE / ON_ERROR（全部）
职责：
  PRE_PIPELINE：记录 Pipeline 开始时间到 ctx.metadata
  PRE_STAGE：   记录 Stage 开始时间到 ctx.metadata
  POST_STAGE：  计算耗时（time.monotonic），logging.info 打印 elapsed_ms
  POST_PIPELINE：打印 Pipeline 总耗时
  ON_ERROR：    logging.error 记录错误（exc_info=True）
```

> Phase 4 再接 OpenTelemetry Span 和 Prometheus Histogram，当前只用标准 logging。

---

## 执行顺序示例（Phase 3 实际行为）

```
PRE_PIPELINE 触发：
  10  TenantGuard    → 校验格式，失败抛 ValueError
  20  QuotaGuard     → 校验配额，失败抛 QuotaExceededError
 100  ObservabilityHook → 记录 Pipeline 开始时间

PRE_STAGE 触发（每个 Stage 前）：
  30  IdempotencyGuard → redis.exists → SKIP_STAGE 或 CONTINUE
 100  ObservabilityHook → 记录 Stage 开始时间

[Stage._execute() 执行]

POST_STAGE 触发（每个 Stage 后）：
  30  IdempotencyGuard → redis.setex 写完成标记
 100  ObservabilityHook → 打印 elapsed_ms

POST_PIPELINE 触发：
 100  ObservabilityHook → 打印 Pipeline 总耗时

ON_ERROR 触发：
 100  ObservabilityHook → 打印错误日志
```

---

## 💡 设计思考

**Hook 和 Middleware 有什么区别？**

Web 框架的 Middleware（如 FastAPI 的 middleware）是针对 HTTP 请求的横切。

Hook 是针对**每个 Stage**的横切，粒度更细，能区分"是哪个 Stage 出了问题"，而不是只知道"这个请求失败了"。

Hook 还有一个 Middleware 没有的能力：`SKIP_STAGE`，可以在运行时跳过某个 Stage。

---

## 下一步

- 了解 Registry 如何管理组件 → [06 Registry](./06-registry.md)
- 了解 Strategy 如何声明 Hook → [08 Strategy](./08-strategy.md)
