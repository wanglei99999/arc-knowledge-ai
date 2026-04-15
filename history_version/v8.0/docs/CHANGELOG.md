# Changelog

本文件记录 ArcKnowledge AI 的所有重要变更。
遵循 [语义化版本](https://semver.org/)，格式基于 [Keep a Changelog](https://keepachangelog.com/)。

---

## [Unreleased]

### Planned — v8.0.0 (Phase 7: 三层记忆系统)

- `app/domain/memory.py` — Session / Message / Memory / MemoryCategory 领域模型
- `app/memory/manager.py` — MemoryManager：记忆全生命周期调度
- `app/memory/assembler.py` — ContextAssembler：token 预算分配（8192 tokens 上限）
- `app/memory/extractor.py` — MemoryExtractor：LLM 事实提取 + 摘要压缩 + ADD/UPDATE/NOOP
- `app/infrastructure/milvus/memory_collection.py` — arc_memories 独立 Milvus collection
- `app/infrastructure/postgres/repositories/session_repo.py` — Session + Message CRUD
- `app/infrastructure/postgres/repositories/memory_repo.py` — Memory CRUD
- `app/services/session_service.py` — 会话管理业务逻辑
- `app/api/routers/session.py` — Session REST API（5 个端点）
- `app/api/dependencies.py` 新增 `require_user()` — 从 JWT `sub` 提取 user_id
- `app/services/chat_service.py` — 接入 ContextAssembler，异步后台记忆提取
- `app/api/routers/chat.py` — ChatRequestBody 新增可选 session_id
- `scripts/migrate.py` — 新增 sessions / messages / memories 三张表

---

## [7.0.0] — 2026-04-13

### Added

- `DocumentStatus.DELETING` / `DELETED` 两个新状态，补全文档生命周期状态机
- `ChunkRepository.get_document_meta()` — 查询文档元数据（含 MinIO file_path）
- `ChunkRepository.delete_chunks_by_document()` — 物理删除指定文档的所有 PG chunks
- `DocumentService.delete()` — 协调四层存储联动删除，混合删除策略
- `DELETE /documents/{document_id}` — 文档删除 REST 接口，返回 `DeleteResult`
- `docs/adr/ADR-020-document-deletion-strategy.md` — 混合删除策略决策记录
- `docs/adr/ADR-021-delete-without-temporal.md` — 删除不用 Temporal 的决策记录

### Changed

- `DocumentStatus` 状态转换表：INDEXED / STALE / FAILED 均可转入 DELETING

---

## [6.0.0] — Phase 5 多模型路由

### Added

- `providers/llm/model_state.py` — 全局 Ollama 模型状态
- `providers/llm/resilient_llm.py` — `ResilientLLMProvider`：tenacity 重试 + fallback 自动切换
- `providers/llm/model_hub.py` — `ModelHub`：按 `ctx.config.llm_provider` 路由
- `api/routers/admin.py` — 模型热切换接口（`/admin/models/switch`）

### Changed

- `providers/llm/ollama_llm.py` — `__init__` 读 `get_current_model()`，支持热切换
- `workflows/rag_orchestrator.py` — `_get_llm_provider()` 改用 `ModelHub`

---

## [5.0.0] — Phase 4 高可用 + 可观测性

### Added

- `infrastructure/telemetry/otel.py` — OpenTelemetry TracerProvider
- `infrastructure/telemetry/metrics.py` — Prometheus metrics
- `k8s/` — Kubernetes 部署配置（namespace / configmap / secret / deployment）
- `GET /metrics` — Prometheus 端点

### Changed

- `pipeline/hooks/observability_hook.py` — 接入 OTel Span + Prometheus

---

## [4.0.0] — Phase 3 多租户 + 横切关注点

### Added

- `pipeline/hooks/tenant_guard.py` — TenantGuard，PRE_PIPELINE
- `pipeline/hooks/quota_guard.py` — QuotaGuard，PRE_PIPELINE
- `pipeline/hooks/idempotency_guard.py` — IdempotencyGuard，Redis TTL 24h
- `pipeline/hooks/observability_hook.py` — 耗时日志
- `infrastructure/redis/client.py` — 异步 Redis 客户端
- `api/dependencies.py` — JWT Bearer Token 验证

---

## [3.0.0] — Phase 2 RAG 检索生成

### Added

- `infrastructure/elasticsearch/client.py` — BM25 检索 + 全文索引
- `pipeline/stages/retrieval/` — 五个检索 Stage（QueryRewrite / VectorSearch / KeywordSearch / RRFFusion / Rerank）
- `pipeline/stages/embedding/es_index_stage.py` — 入库写 ES
- `providers/llm/openai_llm.py` / `ollama_llm.py` — LLM Provider
- `workflows/rag_orchestrator.py` — RAG 全链路协调器
- `api/routers/search.py` — `GET /search`
- `api/routers/chat.py` — `POST /chat`（SSE 流式）

---

## [2.0.0] — Phase 1 完整 Ingestion

### Added

- `infrastructure/minio/client.py` — 文件上传/下载
- `infrastructure/milvus/client.py` — 向量写入/检索
- `pipeline/stages/embedding/milvus_index_stage.py` — 向量写 Milvus
- `providers/parser/paddleocr_provider.py` — 扫描件 OCR
- `scripts/migrate.py` — PostgreSQL 建表（幂等）
- `docker-compose.yml` — 一键启动所有依赖

---

## [1.0.0] — Phase 0 Pipeline 框架

### Added

- `pipeline/core/` — Pipeline 框架四件套（Context / Stage / Pipeline / Registry）
- `domain/document.py` — 领域模型与状态机
- `providers/base.py` — Provider 抽象接口
- `workflows/ingestion_workflow.py` — Temporal 三段式 Ingestion Workflow
- `api/routers/document.py` — `POST /documents/upload` / `GET /documents/{id}/status`
