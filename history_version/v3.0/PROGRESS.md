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

## Phase 3：多租户 + 横切关注点 ⬜

**目标**：激活 Hook 系统，实现租户隔离、配额、幂等、可观测性。

- [ ] `pipeline/hooks/tenant_guard.py`
- [ ] `pipeline/hooks/quota_guard.py`
- [ ] `pipeline/hooks/idempotency_guard.py`
- [ ] `pipeline/hooks/observability_hook.py`
- [ ] `StandardIngestionStrategy.hooks` 填充
- [ ] JWT 验证接入（`api/dependencies.py` 升级）

---

## Phase 4：高可用 + 可观测性 ⬜

- [ ] OpenTelemetry 接入（Jaeger）
- [ ] Prometheus metrics 导出
- [ ] Temporal Workflow 版本化
- [ ] K8s 部署配置

---

## Phase 5：多模型路由 ⬜

- [ ] `ModelHub` 路由策略
- [ ] 熔断 / 降级（`tenacity` 重试）
- [ ] Ollama 本地模型热切换
