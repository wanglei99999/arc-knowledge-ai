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

## Phase 3 会实现的 4 个 Hook

### TenantGuard（priority=10，最先）

```
Phase: PRE_PIPELINE
职责：
  - 验证 tenant_id 在数据库中存在且未被禁用
  - 注入 PG SET LOCAL app.current_tenant_id（激活 RLS）
  - 确保后续所有 DB 操作都带 tenant_id 过滤
```

四道防线中的第二道（第一道是 Context 创建时必须传 tenant_id）。

### QuotaGuard（priority=20）

```
Phase: PRE_PIPELINE + POST_PIPELINE
职责：
  PRE:  检查 ctx.quota.has_api_quota()，不够就 ABORT
  POST: 扣减实际消耗（向 quota_usage 表写入）
```

为什么分 PRE 和 POST？因为不知道这次处理会消耗多少 token，只能先检查"还有没有余量"，处理完再扣实际消耗。

### IdempotencyGuard（priority=30）

```
Phase: PRE_STAGE
职责：
  - 计算 Stage 身份 = hash(document_id + stage_name + content_hash)
  - 如果这个身份在 Redis 里存在 → 返回 SKIP_STAGE
  - 否则 → 返回 CONTINUE，并在 POST_STAGE 后写入 Redis
```

崩溃重试时，已完成的 Stage 会被跳过，不会重复调用 OpenAI API（省钱）。

### ObservabilityHook（priority=100，最后）

```
Phase: PRE_STAGE + POST_STAGE + ON_ERROR
职责：
  - PRE_STAGE:  开始 OpenTelemetry Span
  - POST_STAGE: 结束 Span，记录 Prometheus Histogram（stage 耗时）
  - ON_ERROR:   记录错误日志（带 tenant_id, document_id, stage_name）
```

---

## 执行顺序示例（Phase 3 激活后）

处理一个 Stage 时，Hook 执行顺序：

```
PRE_STAGE 触发：
  10  TenantGuard.handle()    → CONTINUE（租户合法）
  20  QuotaGuard.handle()     → CONTINUE（配额充足）
  30  IdempotencyGuard.handle() → CONTINUE 或 SKIP_STAGE
 100  ObservabilityHook.handle() → CONTINUE（开始计时）

[Stage._execute() 执行]

POST_STAGE 触发：
  10  TenantGuard.handle()    → 不处理 POST_STAGE，跳过
  20  QuotaGuard.handle()     → 不处理 POST_STAGE，跳过
  30  IdempotencyGuard.handle() → 写入完成标记到 Redis
 100  ObservabilityHook.handle() → 结束计时，记录 metrics
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
