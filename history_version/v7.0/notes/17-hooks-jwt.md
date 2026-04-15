# 17 — Phase 3：Hook 实现 + JWT 认证

**对应代码**：`app/pipeline/hooks/` + `app/api/dependencies.py`

## 读完本文你能回答

- 四个 Hook 各自解决什么问题？实际代码做了什么？
- IdempotencyGuard 的 Redis key 格式是什么？TTL 为什么是 24 小时？
- JWT 认证流程是什么？`Depends` 是怎么工作的？
- 非生产环境为什么保留 `X-Tenant-Id` fallback？

---

## 四个 Hook 实现

### TenantGuard（`hooks/tenant_guard.py`）

最简单的一个，只做格式校验：

```python
_TENANT_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]{1,62}[a-zA-Z0-9]$")

class TenantGuard(BaseHook):
    phase = Phase.PRE_PIPELINE
    priority = 10

    async def handle(self, event):
        if not _TENANT_ID_RE.match(event.ctx.tenant_id):
            raise ValueError("Invalid tenant_id format")
        return HookResult.CONTINUE
```

不返回 `ABORT`，直接抛异常——格式错误是调用方 bug，不需要优雅降级。

---

### QuotaGuard（`hooks/quota_guard.py`）

检查配额快照：

```python
class QuotaGuard(BaseHook):
    phase = Phase.PRE_PIPELINE
    priority = 20

    async def handle(self, event):
        quota = event.ctx.quota
        if not quota.has_api_quota():
            raise QuotaExceededError(...)
        if not quota.has_storage_quota():
            raise QuotaExceededError(...)
        return HookResult.CONTINUE
```

`ctx.quota` 是请求开始时固化的快照，同一请求内所有 Stage 看到的配额状态一致。

---

### IdempotencyGuard（`hooks/idempotency_guard.py`）

Phase 3 最核心的 Hook，同时监听 PRE 和 POST 两个时机：

```python
class IdempotencyGuard(BaseHook):
    phase = [Phase.PRE_STAGE, Phase.POST_STAGE]
    priority = 30

    async def handle(self, event):
        key = f"idempotency:{event.ctx.task_id}:{event.stage.name}"
        redis = get_redis()

        if event.phase == Phase.PRE_STAGE:
            if await redis.exists(key):
                return HookResult.SKIP_STAGE   # 已完成，告诉 Pipeline 跳过

        elif event.phase == Phase.POST_STAGE:
            await redis.setex(key, 86400, "1") # 打完成标记，TTL 24h

        return HookResult.CONTINUE
```

**时序**：

```
第一次执行：
  PRE_STAGE  → redis.exists → 不存在 → CONTINUE → 执行 Stage
  POST_STAGE → redis.setex("idempotency:task123:embed_stage", 24h, "1")

Temporal 超时，第二次执行：
  PRE_STAGE  → redis.exists → 存在！→ SKIP_STAGE → Pipeline 跳过此 Stage
```

**TTL 24 小时**：覆盖 Temporal 默认重试窗口，过期后 Redis 自动清理。

---

### ObservabilityHook（`hooks/observability_hook.py`）

监听全部 5 个 Phase，纯记录，永远返回 CONTINUE：

```python
class ObservabilityHook(BaseHook):
    phase = [PRE_PIPELINE, PRE_STAGE, POST_STAGE, POST_PIPELINE, ON_ERROR]
    priority = 100

    async def handle(self, event):
        if event.phase == Phase.PRE_PIPELINE:
            ctx.metadata["_obs_pipeline_start"] = time.monotonic()

        elif event.phase == Phase.PRE_STAGE:
            ctx.metadata[f"_obs_start_{stage.name}"] = time.monotonic()

        elif event.phase == Phase.POST_STAGE:
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.info("[Stage] OK  stage=%-24s elapsed=%.1fms", ...)

        elif event.phase == Phase.POST_PIPELINE:
            logger.info("[Pipeline] DONE  elapsed=%.1fms", ...)

        elif event.phase == Phase.ON_ERROR:
            logger.error("[Pipeline] ERROR  stage=%s error=%s", ..., exc_info=True)

        return HookResult.CONTINUE
```

`priority=100` 排最后，确保业务 Hook（幂等/配额）先跑完再记日志。

---

## JWT 认证（`api/dependencies.py`）

### JWT 是什么

Token 由三段 Base64 组成：

```
eyJhbGciOiJIUzI1NiJ9 . eyJ0ZW5hbnRfaWQiOiJhY21lIn0 . SflKxwRJSMeKKF2QT4fw
      Header                      Payload                     Signature
```

`jwt.decode()` 做两件事：
1. 验签名（用 `jwt_secret` 重算，对不上则 `InvalidTokenError`）
2. 验过期（对比 `exp` claim，过期则 `ExpiredSignatureError`）

### 完整实现

```python
_bearer = HTTPBearer(auto_error=False)

async def require_tenant(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> str:
    # 路径 1：JWT Bearer（生产环境）
    if credentials is not None:
        payload = jwt.decode(
            credentials.credentials,   # "eyJxxx..."（去掉 Bearer 前缀）
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        return str(payload["tenant_id"])

    # 路径 2：X-Tenant-Id header（非生产环境 fallback）
    if settings.app_env != "production" and x_tenant_id:
        return x_tenant_id.strip()

    raise HTTPException(401, "Authentication required")
```

### Depends 如何工作

```python
@router.post("/documents/upload")
async def upload_document(
    tenant_id: str = Depends(require_tenant),   # ← 这一行
):
    ...
```

FastAPI 在执行路由函数前，自动调用 `require_tenant()`：
- 成功 → 返回值注入给 `tenant_id` 参数
- 失败（抛异常）→ 返回 401，路由函数不执行

### auto_error=False 的作用

```python
_bearer = HTTPBearer(auto_error=False)
```

默认 `auto_error=True` 时，没有 Bearer Token 直接抛 401，代码走不到 fallback。
`auto_error=False` 让无 Token 时 `credentials=None`，代码可以继续判断 `X-Tenant-Id`。

---

## Hook 如何挂载到 Pipeline

```python
# standard_strategy.py
class StandardIngestionStrategy(BaseStrategy):
    hooks = [TenantGuard, QuotaGuard, IdempotencyGuard, ObservabilityHook]

    def build_pipeline(self, ...):
        return (Pipeline.start(parser)
                .then(chunker)
                .then(embedder)
                .then(milvus_indexer)
                .then(es_indexer)
                .with_hooks([h() for h in self.hooks]))  # ← Hook 注入点
```

Hook 通过 Strategy 声明，由 Pipeline.with_hooks() 注入，Stage 代码完全不感知。

---

## 下一步

- 了解 Phase 4 如何接 OpenTelemetry → ObservabilityHook 升级
- 了解完整链路 → [16 完整链路追踪 v3](./16-full-flow-v3.md)
