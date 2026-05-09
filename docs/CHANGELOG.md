# Changelog

所有重要变更记录于此文件。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，版本号遵循语义化版本。

---

## [Unreleased]

---

## [ai-4.4.0] - 2026-05-08

### Added

**Phase 13：可观测性完善 + Admin UI**

- `docker-compose.yml`：新增 Prometheus（9090）和 Grafana（3001）服务，volumes 持久化数据
- `monitoring/prometheus/prometheus.yml`：Prometheus scrape 配置，抓取 FastAPI `/metrics` 端点
- `monitoring/grafana/provisioning/`：Grafana 数据源 + Dashboard JSON provisioning（自动加载，无需手动导入）
- `monitoring/grafana/provisioning/dashboards/arc-overview.json`：6 Panel 预置看板（Pipeline 成功率、Stage P95 延迟、LLM Token 速率、费用累计、语义缓存命中率、缓存命中/未命中速率）
- `app/api/routers/admin.py`：`GET /admin/stats` — 系统聚合统计（文档数、切片数、会话数、存储字节）
- `arc-knowledge-web/src/api/admin.ts`：Admin API 函数层（getAdminStats / getTenantUsage / listModelConfigs / upsertModelConfig / deleteModelConfig / getTenantConfig / updateTenantConfig）
- `arc-knowledge-web/src/views/admin/index.vue`：Admin 管理页 — 真实统计卡片 + 7 天 Token 趋势图 + 模型定价 CRUD + 租户 LLM 配置表单

### Changed

- `app/infrastructure/postgres/repositories/usage_repo.py`：`query_by_tenant()` 新增 `group_by: str | None` 参数，`group_by="day"` 时追加 `by_day` 按天分组查询结果
- `app/services/usage_service.py`：`query_by_tenant()` 透传 `group_by` 参数
- `app/api/routers/admin.py`：`GET /admin/tenants/{id}/usage` 新增 `group_by` 查询参数，向后兼容
- `arc-knowledge-web/src/router/index.ts`：新增 `/admin` 路由
- `arc-knowledge-web/src/components/layout/AppSidebar.vue`：导航栏新增"管理配置"入口

### Architecture

- 架构决策见 [ADR-043](adr/ADR-043-prometheus-grafana-observability.md) — Prometheus + Grafana 选型
- 架构决策见 [ADR-044](adr/ADR-044-mock-dashboard-and-real-admin-ui.md) — Demo Dashboard 保留 Mock，Admin 页承载真实数据
- Grafana Dashboard JSON 纳入 Git（provisioning 模式），与代码同生命周期
- Linux 环境下 prometheus.yml 需将 `host.docker.internal` 改为宿主 IP（Docker Desktop 专用）

---

## [ai-4.3.0] - 2026-05-08

### Added

**Phase 12：用量追踪**

- `app/infrastructure/postgres/repositories/usage_repo.py`：`UsageRepository` — `record()` 写入 / `query_by_tenant()` 按租户时段汇总 / `get_today_spend()` 今日费用
- `app/infrastructure/postgres/repositories/model_config_repo.py`：`ModelConfigRepository` — 模型单价 CRUD（`get` / `list_all` / `upsert` / `delete`）
- `app/services/model_config_service.py`：`ModelConfigService` — 模型配置业务逻辑 + `get_cost()` 查单价
- `app/services/usage_service.py`：`UsageService` — 用量查询业务逻辑 + `register_handlers()` 注册 EventBus 订阅者
- `scripts/migrate.py`：新增 `model_configs` 表（模型单价）和 `usage_records` 表（用量流水，含 `cost_usd` 写时快照）
- `app/infrastructure/telemetry/metrics.py`：新增 `arc_llm_tokens_total`（按 tenant/model/type）和 `arc_llm_cost_usd_total`（按 tenant/model）两个 Counter

### Changed

- `app/pipeline/core/events.py`：`DomainEvent.document_id` 改为可选（默认 `""`），支持非文档类事件（如 token 消耗）
- `app/pipeline/core/context.py`：`QuotaSnapshot` 新增 `max_spend_per_day` / `used_spend_today` 字段，新增 `has_spend_quota()` 方法
- `app/pipeline/hooks/quota_guard.py`：新增日费用限额检查；新增 `get_today_spend_cached()` — Redis 缓存（TTL 60s）+ DB 回填
- `app/providers/llm/openai_llm.py`：`stream_generate()` 加 `stream_options={"include_usage": True}`，末尾 chunk 捕获 usage 并发布 `TOKEN_CONSUMED` 事件；`generate()` 同步捕获 `resp.usage`
- `app/providers/llm/ollama_llm.py`：`stream_generate()` 从 `done=true` 末尾 chunk 捕获 `eval_count`；`generate()` 从响应体捕获 token 统计
- `app/api/routers/admin.py`：新增模型配置接口（`GET/POST/DELETE /admin/models/{model_id}`）和用量查询接口（`GET /admin/tenants/{id}/usage`）
- `app/main.py`：lifespan 调用 `register_handlers()` 注册 EventBus 订阅者

### Architecture

- 架构决策见 [ADR-042](adr/ADR-042-usage-tracking-design.md)
- token 捕获通过 EventBus 发布 `TOKEN_CONSUMED` 事件，LLMProvider 接口零变动
- `cost_usd` 写入时按当时单价快照，历史记录不受单价变动影响
- 费用限额为最终一致性执行（Redis 缓存 1 分钟），高并发下可能小幅溢出
- 当前实现为 Python MVP，Java 控制面 `arc-tenant` 就绪后接管计费与限额

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
