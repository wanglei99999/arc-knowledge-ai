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

### ⚠️ 已知设计局限（MVP-B 待修复）

| 问题 | 位置 | 影响 |
|------|------|------|
| 全局可变模型状态 | `providers/llm/model_state.py` | K8s 多副本时各实例状态不一致；所有租户共享同一覆盖值 |
| TenantConfig 硬编码默认值 | `rag_orchestrator.py:64,96,109` | 每次都 `TenantConfig(tenant_id=...)` 用默认值，无法按租户差异化配置 |
| 模型名固定在 `__init__` | `openai_llm.py:21` | OpenAI 无热切换能力；模型与 Provider 实例强耦合 |

---

## Phase 6：文档删除 ✅

**版本目标 v7.0**

**目标**：补全文档生命周期，支持从四个存储中联动删除，解决数据只增不减的问题。

### 修改文件

| 文件 | 改动 |
|------|------|
| `app/domain/document.py` | 新增 `DELETING` / `DELETED` 两个状态及转换规则 |
| `app/infrastructure/postgres/repositories/chunk_repo.py` | 新增 `get_document_meta()` / `delete_chunks_by_document()` |
| `app/services/document_service.py` | 新增 `DeleteResult` dataclass + `delete()` 方法 |
| `app/api/routers/document.py` | 新增 `DELETE /documents/{id}` 接口 |

### 清理链路

```
DELETE /documents/{id}
  → DocumentService.delete()
      1. get_document_meta()          — 查元数据 + file_path
      2. status = DELETING            — 防并发重复删除
      3. MinIO: delete_file()         — 物理删原始文件
      4. Milvus: delete_by_document() — 物理删向量
      5. ES: delete_by_document()     — 物理删全文索引
      6. PG: delete_chunks_by_document() — 物理删 chunks
      7. status = DELETED             — 逻辑删文档记录（保留审计）
```

### 删除策略说明

| 存储层 | 删除方式 | 原因 |
|--------|----------|------|
| MinIO / Milvus / ES / PG chunks | 物理删除 | 派生数据，存储成本高，无独立审计价值 |
| PG documents 记录 | **逻辑删除** | 保留审计（谁、何时、删了什么），防误删，合规要求 |

> 架构决策见 [ADR-020](../docs/adr/ADR-020-document-deletion-strategy.md) 和 [ADR-021](../docs/adr/ADR-021-delete-without-temporal.md)。

---

## Phase 7：三层记忆系统 ✅ 已实现

**版本目标 v8.0**

**目标**：实现工业级三层记忆架构（工作记忆 + 情节记忆 + 语义记忆），彻底解决多轮对话上下文管理问题。参考 mem0 / Zep / MemGPT 的核心设计思路，结合已有 Milvus + ES + PG 基础设施实现。

### 三层记忆架构

```
Layer 1 — 工作记忆（短期，当次会话）
  结构：First-2（锚点）+ Summary（中间摘要，若有）+ Last-N（最近 N 条）
  - First-2：始终保留前 2 条消息，捕获任务目标/用户身份等"锚点"信息
  - Last-N：token 预算内尽可能多保留最近消息（默认 10 条）
  - 无 First-2 的系统在长对话中会丢失初始上下文（如"我是 CFO，在审并购协议"）

Layer 2 — 情节记忆（中期，会话历史压缩）
  消息数 > 20 时，用 LLM 将 First-2 之后、Last-N 之前的中间段压缩为 sessions.summary
  context 顺序：First-2 + summary + Last-N

Layer 3 — 语义记忆（长期，跨会话事实）
  LLM 从对话中提取结构化事实 → PG memories 表 + Milvus arc_memories collection
  查询时语义搜索 top-5 相关记忆注入 context
```

### 新增 DB 表（3 张）

| 表 | 核心字段 | 用途 |
|----|---------|------|
| `sessions` | `session_id`, `tenant_id`, `user_id`, `space_id`, `title`, `summary`, `summary_up_to`, `message_count` | 会话元数据 + 历史摘要 |
| `messages` | `message_id`, `session_id`, `role`, `content`, `token_count` | 消息记录 |
| `memories` | `memory_id`, `tenant_id`, `user_id`, `content`, `category`, `confidence`, `source_session_id`, `vector_id` | 长期语义记忆 |

记忆分类（`category`）：`preference`（偏好）、`fact`（事实）、`context`（上下文）、`entity`（实体）

### 新增文件（10 个）

| 文件 | 说明 |
|------|------|
| `app/domain/memory.py` | `Session` / `Message` / `Memory` / `MemoryCategory` 领域模型 |
| `app/memory/manager.py` | `MemoryManager`：记忆全生命周期调度（存/取/提取/摘要） |
| `app/memory/assembler.py` | `ContextAssembler`：按比例动态分配 token 预算（依据模型 context window），组装最终 LLM context |
| `app/memory/extractor.py` | `MemoryExtractor`：LLM 事实提取 + 历史摘要压缩 + ADD/UPDATE/NOOP 决策 |
| `app/infrastructure/postgres/repositories/session_repo.py` | Session + Message CRUD |
| `app/infrastructure/postgres/repositories/memory_repo.py` | Memory CRUD（按 tenant_id + user_id 查询） |
| `app/infrastructure/milvus/memory_collection.py` | `arc_memories` collection（独立 schema，含 user_id） |
| `app/services/session_service.py` | 会话管理业务逻辑 |
| `app/api/routers/session.py` | Session REST API |

### 修改文件（7 个）

| 文件 | 改动 |
|------|------|
| `app/api/dependencies.py` | 新增 `require_user()` → 返回 `(tenant_id, user_id)`，从 JWT `sub` 提取 |
| `app/services/chat_service.py` | 接入 `ContextAssembler`；`BackgroundTasks` 触发异步记忆提取；新增 `_build_citations()` — 从 RAG 结果构建引用列表，`stream_chat` 返回类型改为 `AsyncIterator[str \| list]`，token 流结束后 yield citations |
| `app/api/routers/chat.py` | `ChatRequestBody` 新增可选 `session_id`；提取 `user_id`；`_sse_stream()` 新增 `list` 分支，将 citations 序列化为 `data: {"citations": [...]}` SSE 事件 |
| `app/infrastructure/postgres/repositories/chunk_repo.py` | `get_chunks_by_ids()` 增加 LEFT JOIN documents，直接返回 `original_name`，省去额外查询 |
| `app/scripts/migrate.py` | 新增 3 张表 |
| `app/main.py` | 注册 session 路由，初始化 `arc_memories` Milvus collection |
| `app/config/settings.py` | 新增 `memory_summarization_threshold=20` / `memory_anchor_messages=2` / `memory_recent_messages=10` / `memory_top_k=5` / `ollama_context_window=8192` |
| `app/providers/base.py` | `LLMProvider` 新增 `get_context_window() -> int` 抽象方法 |

### Chat 链路（新）

```
POST /chat (session_id, user_id)
  → ChatService.stream_chat()
      1. MemoryManager.add_message(user_msg)         ← 存用户消息
      2. MemoryManager.search_memories(query)         ← 语义搜相关长期记忆
      3. RAGOrchestrator.retrieve()                  ← 知识库检索（已有）
      4. ContextAssembler.assemble(...)               ← token 预算组装 context
      5. LLMProvider.stream_generate(context)         ← 流式生成（已有）
      6. MemoryManager.add_message(assistant_msg)    ← 存 AI 回答
      7. BackgroundTask: extract_and_store(...)       ← 后台提取记忆（不阻塞）
      8. BackgroundTask: maybe_summarize(...)         ← 后台摘要压缩（如需）
  → StreamingResponse (SSE)
```

### ContextAssembler Token 预算分配

**预算按比例动态计算**，取决于当前所用模型的 `context_window`（由 `LLMProvider.get_context_window()` 提供）：

| 区域 | 占比 | 优先级 | 说明 |
|------|------|-------|------|
| Response buffer | 固定 500 | 最高 | 始终从总量中预先扣除 |
| System prompt + First-2 | ~8% | 最高（固定） | 始终保留，不参与截断 |
| RAG chunks | 40% | 高 | 知识库检索内容 |
| Last-N 近期消息 | 25% | 高 | token 预算内尽量多取 |
| Long-term memories | 12% | 低 | 跨会话 top-5 语义记忆 |
| Session summary | 6% | 低 | 中间历史的 LLM 摘要 |

各模型实际预算示例：

| 模型 | context window | RAG | Recent | Memories | Summary |
|------|--------------|-----|--------|----------|---------|
| Ollama mistral | 8,192 | 3,077 | 1,923 | 923 | 461 |
| GPT-3.5-turbo | 16,385 | 6,354 | 3,971 | 1,886 | 943 |
| GPT-4o | 128,000 | 51,000 | 31,875 | 15,300 | 7,650 |
| Claude Sonnet | 200,000 | 79,800 | 49,875 | 23,940 | 11,970 |

摘要压缩阈值也随 context window 动态调整：小模型（<16k）= 20 条，中模型（16k–64k）= 80 条，大模型（>64k）= 200 条。

组装顺序：`system → memories → First-2 → summary → Last-N → RAG chunks`

预算不足时从低优先级依次截断（summary → memories → Last-N 尾部）。System prompt 和 First-2 永不截断。

### 新增 Session API

| 接口 | 说明 |
|------|------|
| `POST /sessions` | 创建会话 |
| `GET /sessions` | 列出当前用户所有会话 |
| `GET /sessions/{id}` | 获取会话详情 |
| `DELETE /sessions/{id}` | 删除会话（CASCADE 删消息） |
| `GET /sessions/{id}/messages` | 分页获取消息列表 |

### 用户身份说明

JWT payload 中已包含 `sub`（user_id）字段，无需新增用户系统。`require_user()` 依赖从现有 JWT 中同时提取 `tenant_id` 和 `user_id`；非生产环境 fallback 使用 `tenant_id` 代替 `user_id`。

---

## Phase 7.5：P0 数据库演进 + Citations 完整闭环 ✅

**版本目标 v8.5**

**目标**：完成 P0 数据库 schema 演进，新增 spaces 和 message_citations 表，修复 UUID 主键迁移带来的兼容性问题，实现 citations 从生成到持久化到历史可见的完整闭环。

### 新增表

| 表 | 核心字段 | 用途 |
|----|---------|------|
| `spaces` | `id UUID`, `tenant_id`, `space_key`, `settings JSONB` | 知识空间（双标识：UUID PK + space_key 可读标识） |
| `message_citations` | `message_id UUID`, `document_id UUID`, `chunk_id UUID`, `content_snapshot`, `title_snapshot` | 持久化每次 RAG 回答的引用来源，含快照字段保证历史可溯源 |

### 新增 / 修改文件

| 文件 | 改动 |
|------|------|
| `scripts/migrate.py` | 完整重写为 v2 schema：spaces、message_citations、UUID 主键、documents.file_size、sessions.space_id、部分索引 `idx_documents_active` |
| `infrastructure/postgres/repositories/citation_repo.py` | 新增 `CitationRepository`：`save()` 批量写引用（含 UUID 合法性校验）、`get_by_message_ids()` 批量查询 |
| `infrastructure/postgres/repositories/chunk_repo.py` | 新增 `_row_to_dict()` helper（UUID→str 统一转换）；`create_document` 加 `file_size`；`update_document_status` 转 DELETED 时原子写 `deleted_at` |
| `infrastructure/postgres/repositories/session_repo.py` | `_row_to_session` / `_row_to_message` UUID 字段统一 `str()` 转换 |
| `api/routers/session.py` | `GET /sessions/{id}/messages` 响应新增 `citations` 字段，批量查询后挂载到对应 assistant 消息 |
| `domain/memory.py` | `Session` 新增 `space_id` 字段 |
| `services/session_service.py` | `create()` 透传 `space_id` |
| `api/routers/document.py` | 上传时传 `file_size=len(data)` |
| `memory/extractor.py` | `run()` 加 `citations / space_id` 参数，保存 assistant 消息后调用 `CitationRepository.save()` |
| `services/chat_service.py` | `_build_citations` 补 `chunk_id + source`；`_stream_with_memory` 向 extractor 传 citations |
| `docs/adr/ADR-033` | 数据库演进路线 P0/P1/P2 |
| `docs/adr/ADR-034` | SpaceSettings 工程规则 |
| `docs/adr/ADR-035` | ingestion_jobs 作为 Temporal 运营视图 |
| `docs/design/db-evolution-v2.md` | 完整数据库演进规格 |

### 修复内容

| 问题 | 修复 |
|------|------|
| asyncpg `AmbiguousParameterError` | `deleted_at` 写入改用 Python 条件拼接，避免 CASE 表达式类型歧义 |
| UUID 对象 Pydantic 校验失败 | `_row_to_dict` / `_row_to_session` / `_row_to_message` 统一 UUID→str |
| citations 静默写入失败 | `space_id / document_id / chunk_id` 加 `_valid_uuid()` 校验，非法值降级 None |
| 刷新后 citations 消失 | `GET /sessions/{id}/messages` 批量挂载 citations，前端 `toMessageVO` 映射 citations |

### Citations 完整闭环

```
POST /chat (SSE)
  → ChatService._stream_with_memory()
      → RAGOrchestrator.retrieve()        ← 检索
      → yield token...                    ← 流式输出
      → yield citations                   ← SSE 推送引用（前端实时展示）
      → extractor.run(citations=citations)
          → session_repo.add_message()    ← 保存 assistant 消息，拿 message_id
          → citation_repo.save()          ← 持久化引用到 message_citations

GET /sessions/{id}/messages
  → session_service.get_messages()
  → citation_repo.get_by_message_ids()   ← 批量查 citations
  → MessageOut(citations=[...])          ← 返回给前端（刷新后可见）
```

---

## Phase 8：检索质量提升 ✅

**版本目标 v9.0**

**目标**：实现两个长期 pass-through 的 Stage，真正提升 RAG 召回率与精度。

### 架构修复：SearchContext 统一载体

**问题**：`RRFFusionStage` 原输出 `list[SearchHit]`，导致 `RerankStage` 拿不到 `query_text`，carrier 在此断裂。

**修复**：`SearchContext` 新增 `ranked_hits` 字段，`RRFFusionStage` 填入后继续返回 `SearchContext`，保持 data carrier 贯穿全链路（见 ADR-036）。

| 文件 | 改动 |
|------|------|
| `domain/retrieval.py` | `RetrievalQuery` 加 `intent_is_valid`；`SearchContext` 加 `ranked_hits` |
| `pipeline/core/context.py` | `TenantConfig` 加 `query_rewrite_enabled` / `rerank_provider` |
| `pipeline/stages/retrieval/rrf_fusion_stage.py` | 输出改为 `SearchContext`，填 `ranked_hits` |

### QueryRewriteStage 真实实现

单次 LLM call，JSON 输出，同时完成两件事：
1. **意图识别**：`is_knowledge_query` 写入 `query.intent_is_valid`；`False` 时 VectorSearch/KeywordSearch 跳过检索
2. **Multi-Query 扩展**：生成 3 个语义变体写入 `query.expanded_queries`，VectorSearchStage 对每个变体分别检索后合并去重

任何错误（LLM 超时、JSON 解析失败）自动降级为 passthrough。

| 文件 | 改动 |
|------|------|
| `pipeline/stages/retrieval/query_rewrite_stage.py` | 接入 LLM，意图识别 + 多 Query 扩展 |
| `pipeline/stages/retrieval/vector_search_stage.py` | `intent_is_valid=False` 时跳过 Milvus |
| `pipeline/stages/retrieval/keyword_search_stage.py` | `intent_is_valid=False` 时跳过 ES |

### RerankStage 真实实现

输入改为 `SearchContext`（terminal stage），内部从 PG 拉取 chunk 文本，调用 BGE-Reranker 精排，失败降级为 RRF 顺序。

| 文件 | 改动 |
|------|------|
| `providers/rerank/bge_rerank.py` | BGE-Reranker-v2-m3，`FlagEmbedding` 懒加载 + `run_in_executor`（见 ADR-037）|
| `pipeline/stages/retrieval/rerank_stage.py` | `SearchContext → list[SearchHit]`，调用 RerankProvider |
| `main.py` | 注册 `bge_rerank` Provider |
| `pyproject.toml` | 新增 `FlagEmbedding>=1.2` |

### 调用链路（Phase 8）

```
GET /search?q=...
  → RetrievalService.search()
  → RAGOrchestrator.retrieve()
      → HybridRetrievalStrategy.build_pipeline()
      → QueryRewriteStage   → LLM（意图识别 + 3 变体扩展）
      → VectorSearchStage   → Milvus ANN × N queries，合并去重
      → KeywordSearchStage  → ES BM25
      → RRFFusionStage      → RRF 融合，写入 ranked_hits，返回 SearchContext
      → RerankStage         → PG 取文本 → BGE-Reranker 精排 → list[SearchHit]
  → ChunkRepository.get_chunks_by_ids()
  → 返回 { hits, chunks }
```

---

## Phase 9：测试补全 ✅

**版本目标 v10.0**

**目标**：补全核心模块的零测试状态，覆盖业务关键路径。

### 已完成测试（共 70 个，含原有 21 个）

| 文件 | 用例数 | 覆盖内容 |
|------|--------|---------|
| `tests/unit/pipeline/test_pipeline.py` | 9 | Pipeline 框架、Hook 执行、SubPipeline（Phase 0 原有）|
| `tests/unit/stages/test_token_chunker.py` | 7 | Token 滑窗切片、overlap（Phase 0 原有）|
| `tests/unit/stages/test_embed_stage.py` | 5 | EmbedStage 批量向量化（Phase 0 原有）|
| `tests/unit/hooks/test_tenant_guard.py` | 11 | tenant_id 格式校验、边界值、空值检查 |
| `tests/unit/hooks/test_quota_guard.py` | 8 | API 配额、存储配额、超限优先级 |
| `tests/unit/providers/test_resilient_llm.py` | 7 | 重试次数、fallback 触发、stream 中途失败不回滚 |
| `tests/unit/stages/test_query_rewrite_stage.py` | 8 | 意图识别、Multi-Query 扩展、LLM 失败降级、开关控制 |
| `tests/unit/stages/test_rrf_fusion_stage.py` | 8 | 输出类型为 SearchContext、overlap 加分、top_k、rank 赋值 |
| `tests/unit/stages/test_rerank_stage.py` | 7 | 精排重排、降级 RRF 顺序、provider 不可用、DB 失败降级 |

### 未覆盖（需集成测试环境，留待后续）

| 模块 | 原因 |
|------|------|
| RAGOrchestrator 端到端 | 依赖 Milvus / ES / PG |
| 文档删除四存储联动 | 依赖 MinIO / Milvus / ES / PG |
| 会话持久化 | 依赖 PG |

---

## Phase 10：多租户模型路由重构 ✅

**版本目标 v11.0**

**目标**：修复 Phase 5 全局 model_state 局限，实现三层模型解析，让每个租户独立配置 LLM 引擎和模型。

> 📌 此 Phase 对应原"Phase 6 多租户模型路由"完整设计内容，详见 Phase 5 末尾的⚠️已知局限说明。

### 三层解析优先级

```
请求级 model（/chat body 传入）
  ↓ 未指定则用
TenantConfig.default_llm_model（tenant_configs DB 加载）
  ↓ 为空则用
settings.openai_llm_model / settings.ollama_llm_model（全局兜底）
```

### 新增文件（3 个）

| 文件 | 说明 |
|------|------|
| `infrastructure/postgres/repositories/tenant_config_repo.py` | `TenantConfigRepository`：`get` / `upsert`，按 `tenant_id` PK 操作 |
| `services/tenant_config_service.py` | `TenantConfigService`：`get_or_default`（无记录时 fallback 全默认）/ `update` |
| `docs/adr/ADR-039-per-tenant-model-routing.md` | 废除全局模型状态的架构决策记录 |

### 修改文件（7 个）

| 文件 | 改动 |
|------|------|
| `pipeline/core/context.py` | `TenantConfig` 新增 `default_llm_model: str = ""`、`allowed_models: list[str] = []` |
| `providers/llm/openai_llm.py` | `self._model` → `self._default_model`；新增 `_resolve_model(ctx)` 按调用解析模型 |
| `providers/llm/ollama_llm.py` | 移除 `model_state` 依赖；新增 `_resolve_model(ctx)` |
| `providers/llm/model_hub.py` | 新增 `allowed_models` 白名单校验，违规直接 `PermissionError` |
| `workflows/rag_orchestrator.py` | 三个方法从 `TenantConfigService` 加载配置；新增可选 `model` 参数 |
| `services/chat_service.py` | `ChatRequest` 新增 `model` 字段并透传 |
| `api/routers/chat.py` | `ChatRequestBody` 新增可选 `model` 字段 |
| `api/routers/admin.py` | 删全局 switch 接口；新增 `GET/PUT /admin/tenants/{tenant_id}/config` |
| `scripts/migrate.py` | 新增 `tenant_configs` 表 DDL |

### 删除文件（1 个）

| 文件 | 原因 |
|------|------|
| `providers/llm/model_state.py` | 进程级全局状态，所有租户共享同一模型，多租户隔离漏洞 |

### 已知局限

- `memory/manager.py` 和 `memory/extractor.py` 的记忆提取 LLM 调用仍使用系统默认配置，未接入租户路由（属内部操作，可接受）
- `tenant_configs` 每次 RAG 请求额外一次 PK 查询，后续可加 Redis 缓存

### 调用链路（Phase 10）

```
POST /chat (body.model 可选)
  → ChatService.stream_chat(req.model)
  → RAGOrchestrator.retrieve/stream_generate(model=req.model)
      → TenantConfigService.get_or_default(tenant_id)   ← DB 加载
      → if model: replace(config, default_llm_model=model)  ← 请求级覆盖
      → ModelHub.get_provider(ctx)
          → allowed_models 校验
          → OpenAILLMProvider / OllamaLLMProvider
              → _resolve_model(ctx)  ← ctx.config.default_llm_model or settings 默认

PUT /admin/tenants/{tenant_id}/config  ← 租户级配置 CRUD（替换原全局 switch）
```

---

## Phase 11a：限流中间件 ✅

**版本目标 v12.0**

**目标**：防滥用，按租户隔离请求频率，保护 LLM 调用成本。

### 新增文件（2 个）

| 文件 | 说明 |
|------|------|
| `api/middleware/__init__.py` | middleware 包 |
| `api/middleware/rate_limit.py` | 纯 ASGI 限流中间件：滑动窗口计数器，按 `tenant_id` 隔离，Redis 不可用时 fail open |

### 修改文件（2 个）

| 文件 | 改动 |
|------|------|
| `config/settings.py` | 新增 `rate_limit_enabled`（bool）、`rate_limit_per_minute`（默认 60） |
| `main.py` | 注册 `RateLimitMiddleware`，位于 `CORSMiddleware` 内层 |

### 算法：滑动窗口计数器

```
now_ts      = int(time.time())
cur_bucket  = now_ts // 60          # 当前分钟桶
elapsed     = now_ts % 60           # 当前分钟已过秒数

cur_count   = INCR rate:{tenant_id}:{cur_bucket}
prev_count  = GET  rate:{tenant_id}:{cur_bucket - 1}

weight      = 1.0 - elapsed / 60.0  # 前一桶权重线性衰减
estimated   = cur_count + prev_count × weight

if estimated > limit → 429 Too Many Requests
```

### 响应头

| 场景 | 响应头 |
|------|-------|
| 正常放行 | `X-RateLimit-Limit` / `X-RateLimit-Remaining` / `X-RateLimit-Reset` |
| 超限 | `HTTP 429` + `Retry-After: {剩余秒数}` |

### 已知局限

- 限额为全局统一值，尚不支持按租户差异化配额（后续可在 `TenantConfig` 扩展 `rate_limit_per_minute` 字段）
- Sliding Window Counter 为近似算法，极端流量模式下误差 < 5%

---

## Phase 11b：语义缓存 ✅

**版本目标 v13.0**

**目标**：降低重复问题的 LLM 调用成本，首轮语义相近问题跳过检索 + LLM。

### 新增文件（1 个）

| 文件 | 说明 |
|------|------|
| `infrastructure/redis/semantic_cache.py` | `SemanticCache`：两级缓存（精确 hash + 向量 KNN）、EmbeddingProvider 注入、异步写、Prometheus metrics |

### 修改文件（6 个）

| 文件 | 改动 |
|------|------|
| `infrastructure/redis/client.py` | 新增 `get_redis_binary()`（`decode_responses=False`） |
| `config/settings.py` | 新增 4 个 `semantic_cache_*` 配置项 |
| `workflows/rag_orchestrator.py` | 新增 `chat()` 门面方法 + `_build_citations()` 静态方法 |
| `services/chat_service.py` | `_stream_simple()` 透传 `orchestrator.chat()`（3 行） |
| `services/document_service.py` | `delete()` 触发 `cache.invalidate(tenant_id)` |
| `main.py` | lifespan 调用 `cache.initialize()` |
| `docker-compose.yml` | Redis 升级为 `redis/redis-stack-server:latest` |

### 核心设计

```
RAGOrchestrator.chat()
  ├─ history=[] → normalize → hash 精确匹配（O(1)）
  │                         → embed → KNN 向量搜索（score ≥ 0.90 命中）
  └─ history 非空 → 直接 RAG（多轮不走缓存）
```

### 失效策略

| 操作 | 是否失效 |
|------|---------|
| 文档删除 | ✅（chunk_ids 断裂） |
| 文档重传 | ✅（chunk_ids 全换） |
| 新增文档 | ❌ |
| 仅更新元数据 | ❌ |

### 架构决策

见 [ADR-041](docs/adr/ADR-041-semantic-cache-design.md)

---

## Phase 12：用量追踪 📋 设计中

**版本目标 v13.0**

**目标**：记录每次 LLM 调用的 token 消耗与费用，为配额管理和成本分析提供数据基础。

> 📌 此 Phase 对应原"Phase 7 模型用量追踪"完整设计内容。

### 新增 DB 表

| 表 | 核心字段 |
|----|---------|
| `model_configs` | `model_id`, `input_cost_per_1k_tokens`, `output_cost_per_1k_tokens`, `context_window` |
| `usage_records` | `tenant_id`, `model_id`, `input_tokens`, `output_tokens`, `cost_usd`, `created_at` |

### 关键改动

- `pipeline/hooks/observability_hook.py` — POST_PIPELINE 解析 LLM usage，写 `usage_records`
- `pipeline/core/context.py` — `QuotaSnapshot` 新增 `max_spend_per_day`、`used_spend_today`
- 新增 `GET /admin/tenants/{id}/usage` 和 `GET /admin/models` 接口

---

## Phase 13：可观测性完善 + Admin UI 📋 设计中

**版本目标 v14.0**

**目标**：让系统具备生产运营能力。

### 可观测性

- Grafana 看板：Stage 耗时 / Pipeline 成功率 / LLM 延迟 / 用量趋势
- Prometheus AlertRule：延迟 P95 > 3s / 错误率 > 5% / LLM 连续失败

### 最小 Admin UI

- 文档列表管理（上传 / 删除 / 状态）
- 租户模型配置
- 用量查看

---

## Phase 14+：Java 控制面

**条件**：Python MVP 稳定、有真实多用户接入需求时启动。

```
arc-gateway   → 统一入口、JWT 验证、限流
arc-auth      → OAuth2 / SSO / MFA
arc-tenant    → 租户配置、配额、计费（接管 Python 临时替代品）
arc-knowledge → 知识空间权限、文档元数据
arc-user      → 用户 / 组织 / RBAC
arc-audit     → 审计日志
```

届时 Python 侧的 `require_tenant()` / `QuotaGuard` 等临时鉴权逻辑由 Java 控制面正式接管。
