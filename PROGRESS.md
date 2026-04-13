# ArcKnowledge AI — 开发进度

> 每个 Phase 完成后更新此文件。

---

## Phase 0：Pipeline 框架 + 单文档处理 ✅

**目标**：建立核心抽象，跑通"上传→解析→切片→向量化→写库"完整链路。

### 已完成文件

#### 核心框架（`app/pipeline/core/`）
| 文件 | 说明 |
|------|------|
| `context.py` | `ProcessingContext` / `QuotaSnapshot` / `TenantConfig` |
| `stage.py` | `BaseStage`（requires / produces / precondition check） |
| `pipeline.py` | `Pipeline`（run / then / as_stage / with_hooks） |
| `hook.py` | `BaseHook` / `Phase` / `HookResult` / `HookRunner` |
| `registry.py` | `ComponentRegistry` 单例（stage / provider / strategy 三张表） |
| `events.py` | `DomainEvent` / `EventBus` |
| `exceptions.py` | 所有自定义异常 |

#### 领域模型（`app/domain/`）
| 文件 | 说明 |
|------|------|
| `document.py` | `RawFile` / `DocumentChunk` / `Document` / `DocumentStatus` 状态机 |

#### Stage 实现（`app/pipeline/stages/`）
| 文件 | 说明 |
|------|------|
| `parsing/parser_stage.py` | 通用解析 Stage，委托给 `ParserProvider` |
| `chunking/token_chunker.py` | Token 估算滑窗切片，支持 overlap |
| `embedding/embed_stage.py` | 批量向量化，分批处理（100条/批） |

#### Provider 实现（`app/providers/`）
| 文件 | 说明 |
|------|------|
| `base.py` | `EmbeddingProvider` / `LLMProvider` / `ParserProvider` / `RerankProvider` 抽象接口 |
| `parser/unstructured_provider.py` | 基于 Unstructured 库，支持 PDF/Word/Excel/HTML 等 |
| `embedding/openai_embedding.py` | OpenAI `text-embedding-3-small`，按 index 排序保证顺序 |

#### Strategy（`app/pipeline/strategies/`）
| 文件 | 说明 |
|------|------|
| `base_strategy.py` | `BaseStrategy` ABC，声明 hooks 列表 |
| `ingestion/standard_strategy.py` | `StandardIngestionStrategy`：parser → chunker → embedder |

#### 基础设施（`app/infrastructure/`）
| 文件 | 说明 |
|------|------|
| `postgres/client.py` | SQLAlchemy 异步连接池，`get_session()` 上下文管理器 |
| `postgres/repositories/chunk_repo.py` | `save_chunks()` / `update_document_status()` / `get_chunks_by_document()` |
| `temporal/worker.py` | Temporal Worker 启动，注册 Workflow + 3 个 Activity |

#### 工作流（`app/workflows/`）
| 文件 | 说明 |
|------|------|
| `ingestion_activities.py` | `parse_activity` / `chunk_activity` / `embed_and_index_activity` |
| `ingestion_workflow.py` | `IngestionWorkflow`：三个 Activity 顺序执行，各自独立重试 |

#### 服务 / 接口（`app/services/` + `app/api/`）
| 文件 | 说明 |
|------|------|
| `services/document_service.py` | `ingest()` 触发 Workflow，`get_status()` 查询进度 |
| `api/routers/document.py` | `POST /documents/upload`，`GET /documents/{id}/status` |
| `api/dependencies.py` | `require_tenant()`：从 `X-Tenant-Id` 头提取租户 ID |
| `config/settings.py` | Pydantic Settings，读取 `.env` 文件 |
| `main.py` | FastAPI 启动，lifespan 注册所有组件 |

#### 测试（`tests/`）
| 文件 | 用例数 |
|------|-------|
| `unit/pipeline/test_pipeline.py` | 9 个 Pipeline 框架测试 |
| `unit/stages/test_token_chunker.py` | 7 个切片测试 |
| `unit/stages/test_embed_stage.py` | 5 个 Embedding 测试（FakeProvider） |
| `conftest.py` | `fake_ctx` / `quota` / `tenant_config` fixtures |

### 调用链路

```
POST /documents/upload
  → DocumentService.ingest()
  → Temporal: IngestionWorkflow
      ├── parse_activity  →  ParserStage  →  UnstructuredParserProvider
      ├── chunk_activity  →  TokenChunkerStage
      └── embed_and_index_activity
              →  EmbedStage  →  OpenAIEmbeddingProvider
              →  ChunkRepository  →  PostgreSQL
```

### 启动方式

```bash
cp .env.example .env        # 填入 OPENAI_API_KEY 等

# 终端 1：FastAPI
uvicorn app.main:app --reload

# 终端 2：Temporal Worker
python scripts/start_worker.py
```

---

## Phase 1：完整 Ingestion ✅

**目标**：接入真实存储，支持 PDF / Word / Excel 多格式。

- [x] `infrastructure/minio/client.py` — 文件上传/下载，boto3 + run_in_executor
- [x] `infrastructure/milvus/client.py` — 向量写入/检索，tenant_id 作 Partition Key
- [x] `pipeline/stages/embedding/milvus_index_stage.py` — 向量写 Milvus Stage
- [x] `providers/parser/paddleocr_provider.py` — 扫描件 OCR，置信度过滤
- [x] `pipeline/strategies/ingestion/ocr_strategy.py` — OCR 专用策略
- [x] `scripts/migrate.py` — PostgreSQL 建表（幂等）
- [x] `docker-compose.yml` — PostgreSQL / MinIO / Milvus / Redis / Temporal 一键启动

**重构**：
- [x] `StandardIngestionStrategy` — 加入 `MilvusIndexStage`（embed → milvus）
- [x] `embed_and_index_activity` — 走 `Pipeline.start(embedder).then(milvus_indexer)`
- [x] `api/routers/document.py` — 真实上传到 MinIO，传 object_key 给 Workflow
- [x] `services/document_service.py` — 支持外部传入 `document_id`
- [x] `main.py` — 注册 `milvus_index_stage` / `paddleocr_parser` / `ocr` strategy

### 调用链路（Phase 1）

```
POST /documents/upload
  → 读取文件字节
  → MinIO upload_file()                   ← 新增
  → DocumentService.ingest()
  → Temporal: IngestionWorkflow
      ├── parse_activity  →  ParserStage  →  UnstructuredParserProvider
      ├── chunk_activity  →  TokenChunkerStage
      └── embed_and_index_activity
              →  EmbedStage   →  OpenAIEmbeddingProvider
              →  MilvusIndexStage  →  Milvus  ← 新增
              →  ChunkRepository  →  PostgreSQL
```

### 启动方式

```bash
# 1. 启动所有依赖
docker-compose up -d

# 2. 建表
python scripts/migrate.py

# 3. 启动服务
cp .env.example .env   # 填入 OPENAI_API_KEY
uvicorn app.main:app --reload

# 4. 启动 Temporal Worker
python scripts/start_worker.py
```

---

## Phase 2：RAG 检索生成 ✅

**目标**：向量检索 + LLM 流式问答。

- [x] `infrastructure/milvus/client.py` — ANN 检索（Phase 1 已实现 `search_vectors()`）
- [x] `infrastructure/elasticsearch/client.py` — BM25 检索（`index_chunks` + `bm25_search` + `delete_by_document`）
- [x] `pipeline/stages/retrieval/query_rewrite_stage.py` — 查询改写（pass-through，Phase 3 接 LLM）
- [x] `pipeline/stages/retrieval/vector_search_stage.py` — Milvus ANN 检索
- [x] `pipeline/stages/retrieval/keyword_search_stage.py` — ES BM25 检索
- [x] `pipeline/stages/retrieval/rrf_fusion_stage.py` — Reciprocal Rank Fusion 融合
- [x] `pipeline/stages/retrieval/rerank_stage.py` — Rerank（pass-through，Phase 3 接 RerankProvider）
- [x] `pipeline/stages/embedding/es_index_stage.py` — 入库时写 ES
- [x] `pipeline/strategies/retrieval/hybrid_strategy.py` — 混合检索策略
- [x] `providers/llm/openai_llm.py` — OpenAI LLM（流式 + 非流式）
- [x] `providers/llm/ollama_llm.py` — Ollama 本地 LLM
- [x] `workflows/rag_orchestrator.py` — RAG 全链路协调器
- [x] `services/retrieval_service.py` — 检索服务
- [x] `services/chat_service.py` — 问答服务
- [x] `api/routers/search.py` — `GET /search`
- [x] `api/routers/chat.py` — `POST /chat`（SSE 流式）

**重构**：
- [x] `StandardIngestionStrategy` — 加入 `ESIndexStage`（同时写 Milvus + ES）
- [x] `main.py` — 注册 Retrieval Stage / LLM Provider / Hybrid Strategy

### 调用链路（Phase 2）

```
GET /search?q=...
  → RetrievalService.search()
  → RAGOrchestrator.retrieve()
      → HybridRetrievalStrategy.build_pipeline()
      → QueryRewriteStage（pass-through）
      → VectorSearchStage  → Milvus ANN
      → KeywordSearchStage → Elasticsearch BM25
      → RRFFusionStage     → 融合排序
      → RerankStage        → 精排（pass-through）
  → ChunkRepository.get_chunks_by_ids() → PostgreSQL
  → 返回 { hits, chunks }

POST /chat
  → ChatService.stream_chat()
  → RAGOrchestrator.retrieve()   ← 同上
  → RAGOrchestrator.stream_generate()
      → LLMProvider.stream_generate()  → OpenAI / Ollama
  → StreamingResponse (SSE)
```

### 新增接口

| 接口 | 说明 |
|------|------|
| `GET /search?q=...&space_id=...&top_k=10` | 混合检索，返回 chunk 列表 |
| `POST /chat` | RAG 问答，SSE 流式输出 |

---

## Phase 3：多租户 + 横切关注点 ✅

**目标**：激活 Hook 系统，实现租户隔离、配额、幂等、可观测性。

- [x] `pipeline/hooks/tenant_guard.py` — TenantGuard，PRE_PIPELINE，priority=10，格式校验
- [x] `pipeline/hooks/quota_guard.py` — QuotaGuard，PRE_PIPELINE，priority=20，API/存储配额检查
- [x] `pipeline/hooks/idempotency_guard.py` — IdempotencyGuard，PRE+POST_STAGE，priority=30，Redis TTL 24h
- [x] `pipeline/hooks/observability_hook.py` — ObservabilityHook，全 Phase，priority=100，耗时日志
- [x] `StandardIngestionStrategy.hooks` + `OCRIngestionStrategy.hooks` 已填充四个 Hook
- [x] `infrastructure/redis/client.py` — 异步 Redis 客户端（连接池）
- [x] `api/dependencies.py` — JWT 验证（Bearer Token），非生产环境保留 X-Tenant-Id fallback
- [x] `config/settings.py` — 新增 `jwt_secret` / `jwt_algorithm` / `jwt_expire_minutes`

### Hook 执行顺序（每次 Pipeline 运行）

```
PRE_PIPELINE：  TenantGuard(10) → QuotaGuard(20)
PRE_STAGE：     IdempotencyGuard(30) → ObservabilityHook(100)
POST_STAGE：    IdempotencyGuard(30，写 Redis） → ObservabilityHook(100，记耗时)
POST_PIPELINE： ObservabilityHook(100，记总耗时)
ON_ERROR：      ObservabilityHook(100，记错误)
```

---

## Phase 4：高可用 + 可观测性 ✅

**目标**：接入生产级可观测性，支持安全滚动升级和容器化部署。

- [x] `infrastructure/telemetry/otel.py` — OTel TracerProvider，OTLP gRPC 导出（Jaeger）
- [x] `infrastructure/telemetry/metrics.py` — Prometheus Histogram（stage 耗时）+ Counter（pipeline 总数）
- [x] `pipeline/hooks/observability_hook.py` — 升级：OTel Span + Prometheus metrics + logging
- [x] `config/settings.py` — 新增 `otel_enabled` / `otel_service_name` / `otlp_endpoint`
- [x] `main.py` — lifespan 初始化 OTel，新增 `GET /metrics` Prometheus 端点
- [x] `workflows/ingestion_workflow.py` — `workflow.patched("phase4-three-stage-pipeline")` 版本标记
- [x] `k8s/namespace.yaml` — arc-knowledge 命名空间
- [x] `k8s/configmap.yaml` — 非敏感配置（环境变量）
- [x] `k8s/secret.yaml` — 敏感配置模板（openai_key / jwt_secret 等）
- [x] `k8s/api-deployment.yaml` — FastAPI Deployment（2 副本）+ ClusterIP Service
- [x] `k8s/worker-deployment.yaml` — Temporal Worker Deployment（2 副本）
- [x] `k8s/jaeger.yaml` — Jaeger all-in-one（开发用，生产换 Tempo）

### 可观测性链路

```
Pipeline 运行
  → ObservabilityHook
      ├── OTel Span → OTLP → Jaeger（分布式追踪）
      ├── Prometheus Histogram → /metrics → Prometheus → Grafana
      └── logging → 标准输出 → 日志收集器
```

### 新增接口

| 接口 | 说明 |
|------|------|
| `GET /metrics` | Prometheus metrics 端点（不在 Swagger 显示） |

---

## Phase 5：多模型路由 ✅

**目标**：LLM Provider 智能路由，支持熔断降级和 Ollama 热切换。

- [x] `providers/llm/model_state.py` — 模块级 Ollama 模型状态，`set_current_model()` / `get_current_model()`
- [x] `providers/llm/resilient_llm.py` — `ResilientLLMProvider`：tenacity 重试 + fallback 自动切换
- [x] `providers/llm/model_hub.py` — `ModelHub`：按 ctx.config.llm_provider 路由，主备相同时跳过包装
- [x] `providers/llm/ollama_llm.py` — `__init__` 改为读 `get_current_model()`，支持热切换
- [x] `workflows/rag_orchestrator.py` — `_get_llm_provider()` 改用 `model_hub.get_provider(ctx)`
- [x] `api/routers/admin.py` — `POST /admin/models/switch`、`DELETE /admin/models/switch`、`GET /admin/models/current`
- [x] `config/settings.py` — 新增 `llm_fallback_provider` / `llm_max_retries` / `llm_retry_min_wait` / `llm_retry_max_wait`
- [x] `pyproject.toml` — 新增 `tenacity>=8.3`

### 熔断降级流程

```
RAGOrchestrator._get_llm_provider(ctx)
  → ModelHub.get_provider(ctx)
      → ResilientLLMProvider(primary=openai_llm, fallback=ollama_llm)

generate() 调用：
  tenacity 重试（最多 3 次，指数退避 1~10s）
    → 全部失败 → 自动切换 fallback Provider

stream_generate() 调用：
  主 Provider 首个 token 前失败 → 自动切换 fallback
  已开始推送 token 后失败      → 直接报错（无法回滚）
```

### 新增接口

| 接口 | 说明 |
|------|------|
| `POST /admin/models/switch?model=mistral` | 热切换 Ollama 模型 |
| `DELETE /admin/models/switch` | 重置为 settings 默认 |
| `GET /admin/models/current` | 查询当前生效模型 |
