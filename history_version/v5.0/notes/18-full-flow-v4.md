# 18 — 全链路追踪 v4（Phase 3）

> **实现版本**：Phase 3（v4.0）
> Phase 2 的链路见 [16-full-flow-v3.md](./16-full-flow-v3.md)。
> 本文只描述 Phase 3 新增/变更的部分：**JWT 认证** 和 **Hook 系统激活**。

---

## Phase 3 相比 Phase 2 的变化

| 位置 | Phase 2 | Phase 3 |
|------|---------|---------|
| API 认证 | `X-Tenant-Id` header，无验证 | JWT Bearer Token 验证，非生产保留 fallback |
| Pipeline 执行前 | 直接跑 Stage | PRE_PIPELINE Hook：TenantGuard → QuotaGuard |
| 每个 Stage 前 | 直接执行 | PRE_STAGE Hook：IdempotencyGuard → ObservabilityHook |
| 每个 Stage 后 | 无 | POST_STAGE Hook：IdempotencyGuard 写 Redis → ObservabilityHook 记耗时 |
| Stage 失败时 | 直接抛异常 | ON_ERROR Hook：ObservabilityHook 记错误日志 |

---

## 变化点 1：API 层 JWT 认证

### Phase 2 的方式
```
POST /documents/upload
Headers: X-Tenant-Id: tenant-abc   ← 任何人都能伪造
```

### Phase 3 的方式
```
POST /documents/upload
Headers: Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJ0ZW5hbnRfaWQiOiJ0ZW5hbnQtYWJjIn0.xxx
```

**`require_tenant()` 执行流程**：

```
请求进来
  ↓
HTTPBearer 解析 Authorization header
  ├── 有 Bearer Token
  │     ↓
  │   jwt.decode(token, jwt_secret, algorithms=["HS256"])
  │     ├── 签名验证通过 + 未过期
  │     │     ↓
  │     │   payload["tenant_id"] → 返回 "tenant-abc"
  │     ├── 已过期 → HTTP 401 "Token has expired"
  │     └── 签名错误 → HTTP 401 "Invalid token"
  │
  └── 无 Bearer Token
        ├── app_env != "production" 且有 X-Tenant-Id header
        │     → 返回 header 值（开发 fallback）
        └── 否则 → HTTP 401 "Authentication required"
```

---

## 变化点 2：Pipeline 执行加入 Hook 系统

以文档上传的 `embed_and_index_activity` 为例，Phase 3 后每次 Pipeline 运行：

### PRE_PIPELINE（整条 Pipeline 开始前，触发一次）

```
HookRunner.fire(PRE_PIPELINE)
  ↓
TenantGuard(priority=10).handle()
  → 正则校验 tenant_id 格式
  → 格式非法：raise ValueError → Pipeline 中止
  → 格式合法：return CONTINUE

QuotaGuard(priority=20).handle()
  → 检查 ctx.quota.has_api_quota()
  → 超限：raise QuotaExceededError → Pipeline 中止
  → 充足：return CONTINUE

ObservabilityHook(priority=100).handle()
  → ctx.metadata["_obs_pipeline_start"] = time.monotonic()
  → logger.info("[Pipeline] START ...")
  → return CONTINUE
```

### 每个 Stage 的执行（以 EmbedStage 为例）

```
HookRunner.fire(PRE_STAGE, stage=embed_stage)
  ↓
IdempotencyGuard(priority=30).handle()
  → key = "idempotency:task-abc123:embed_stage"
  → redis.exists(key)
      ├── 存在（Temporal 重试）→ return SKIP_STAGE
      │     ↓
      │   Pipeline 直接 continue，跳过 EmbedStage
      └── 不存在（首次执行）→ return CONTINUE

ObservabilityHook(priority=100).handle()
  → ctx.metadata["_obs_start_embed_stage"] = time.monotonic()
  → return CONTINUE

──────────────────────────────────────
EmbedStage._execute() 执行（调 OpenAI API）
──────────────────────────────────────

HookRunner.fire(POST_STAGE, stage=embed_stage)
  ↓
IdempotencyGuard(priority=30).handle()
  → redis.setex("idempotency:task-abc123:embed_stage", 86400, "1")
  → return CONTINUE

ObservabilityHook(priority=100).handle()
  → elapsed_ms = (time.monotonic() - start) * 1000
  → logger.info("[Stage] OK  stage=embed_stage  elapsed=1234.5ms ...")
  → return CONTINUE
```

### POST_PIPELINE（整条 Pipeline 结束后）

```
HookRunner.fire(POST_PIPELINE)
  ↓
ObservabilityHook(priority=100).handle()
  → elapsed_ms = (time.monotonic() - pipeline_start) * 1000
  → logger.info("[Pipeline] DONE  elapsed=2345.6ms ...")
```

### ON_ERROR（任意 Stage 抛异常时）

```
HookRunner.fire(ON_ERROR, stage=embed_stage, error=TimeoutError(...))
  ↓
ObservabilityHook(priority=100).handle()
  → logger.error("[Pipeline] ERROR  stage=embed_stage ...")
  → 原始异常继续向上抛出（Temporal 捕获并重试）
```

---

## 完整请求链路（Phase 3）

```
POST /documents/upload
  Authorization: Bearer <JWT>
      ↓
  require_tenant()
    jwt.decode() → tenant_id = "tenant-abc"
      ↓
  DocumentService.ingest()
      ↓
  Temporal: IngestionWorkflow
      ├── parse_activity
      │     Pipeline.run(hooks=[TenantGuard, QuotaGuard, IdempotencyGuard, ObservabilityHook])
      │       PRE_PIPELINE: TenantGuard → QuotaGuard → ObservabilityHook
      │       PRE_STAGE(parser): IdempotencyGuard(check) → ObservabilityHook(start)
      │         → ParserStage._execute()
      │       POST_STAGE(parser): IdempotencyGuard(write Redis) → ObservabilityHook(log)
      │       POST_PIPELINE: ObservabilityHook(total time)
      │
      ├── chunk_activity（同上，Hook 逐 Stage 触发）
      │
      └── embed_and_index_activity（同上）
              EmbedStage → MilvusIndexStage → ESIndexStage
              ↑ 每个 Stage 前后都有 IdempotencyGuard + ObservabilityHook
```

---

## 下一步

- 了解 Hook 实现细节 → [17-hooks-jwt.md](./17-hooks-jwt.md)
- 了解 Phase 4 可观测性升级（OTel Span + Prometheus）→ ObservabilityHook 在 v5 中的副作用不改变此链路，仅在 POST_STAGE 额外发送 Span 和 metrics
