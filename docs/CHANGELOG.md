# Changelog

所有重要变更记录于此文件。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，版本号遵循语义化版本。

---

## [Unreleased]

---

## [ai-4.2.0] - 2026-05-07

### Added

**Phase 11b：语义缓存**

- `app/infrastructure/redis/semantic_cache.py`：`SemanticCache` 核心实现
  - 两级缓存：精确匹配（MD5 hash）→ 语义向量匹配（HNSW KNN）
  - 查询标准化（normalize）提升命中率
  - 每租户独立 Redis Stack 向量索引，失效时 `FT.DROPINDEX` 一行清除
  - EmbeddingProvider 懒加载注入，`get_dimension()` 动态读取，不硬编码维度
  - 异步写缓存（`asyncio.create_task`），不阻塞流式输出
  - Prometheus metrics：`semantic_cache_hits_total` / `semantic_cache_misses_total` / `semantic_cache_score`
- `app/infrastructure/redis/client.py`：新增 `get_redis_binary()`（`decode_responses=False`），供向量二进制存储使用
- `app/workflows/rag_orchestrator.py`：新增 `chat()` 门面方法（含缓存逻辑）和 `_build_citations()` 静态方法

### Changed

- `app/services/chat_service.py`：`_stream_simple()` 改为透传 `_orchestrator.chat()`，自身不再持有缓存逻辑
- `app/services/document_service.py`：`delete()` 末尾加 `cache.invalidate(tenant_id)`，文档删除时同步清除语义缓存
- `app/config/settings.py`：新增 `semantic_cache_enabled` / `semantic_cache_threshold` / `semantic_cache_ttl_seconds` / `semantic_cache_embedding_provider`
- `app/main.py`：lifespan 在 `_register_components()` 之后调用 `cache.initialize()`
- `docker-compose.yml`：Redis 镜像从 `redis:7-alpine` 升级为 `redis/redis-stack-server:latest`

### Architecture

- 架构决策见 [ADR-041](adr/ADR-041-semantic-cache-design.md)
- 失效规则：文档**删除**或**重传**时失效，新增文档不触发失效
- 缓存仅对 `history=[]` 的首轮问答生效，多轮对话绕过缓存

---

## [ai-4.1.0] - 2026-05-06

### Added

**Phase 11a：限流中间件**

- `app/api/middleware/rate_limit.py`：纯 ASGI 限流中间件
  - 滑动窗口计数器算法（两个 Redis 分钟桶 + 线性权重，误差 < 5%）
  - 按 `tenant_id` 隔离（从 JWT Bearer token 或 `X-Tenant-Id` Header 提取）
  - 跳过 OPTIONS / `/health` / `/metrics` / `/docs` 等非业务端点
  - Redis 不可用时 fail open（记录警告，请求正常通过）
  - 超限返回 `HTTP 429` + `Retry-After` Header
  - 正常放行注入 `X-RateLimit-Limit` / `X-RateLimit-Remaining` / `X-RateLimit-Reset`

### Changed

- `app/config/settings.py`：新增 `rate_limit_enabled`（默认 `true`）和 `rate_limit_per_minute`（默认 60）
- `app/main.py`：注册 `RateLimitMiddleware`（位于 `CORSMiddleware` 内层，CORS 预检后执行）

### Architecture

- 架构决策见 [ADR-040](adr/ADR-040-rate-limiting-sliding-window.md)
- 纯 ASGI 实现兼容 SSE 流式输出（`BaseHTTPMiddleware` 会缓冲全部 chunks，破坏 SSE）
