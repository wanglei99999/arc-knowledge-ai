# Incipit AI — Data Plane

Incipit 多租户知识库系统的数据面服务，负责文档入库（Ingestion）、知识检索（Retrieval）与 RAG 问答。仓库和数据命名空间继续保留历史名称 `arc-knowledge-ai` / `arc_*`。

当前主线已完成 **Phase 0–15（v15.x）**：文档入库与删除、智能解析、混合检索、Query Rewrite、Rerank、RAG 流式问答、会话与附件生命周期、三层记忆、知识空间、租户模型路由、限流、语义缓存、用量追踪、可观测性、Admin UI 和可复现的本地容器运行链路均已落地。

项目现阶段进入**收口与真实使用验证**：优先处理类型检查基线、RAG 质量评测、真实浏览器全链路、备份恢复与跨机器复现。Tenant / Group / Space 细粒度授权已有设计但仍待审核；Java 控制面仅在出现真实多用户和独立治理需求时启动。

---

## 架构总览

系统分为六层，每层职责单一，依赖方向严格向下：

```mermaid
graph TD
    A["Interface Layer\nFastAPI Router"]
    B["Application Layer\nDocumentService / RetrievalService / ChatService"]
    C["Orchestration Layer\nTemporal Workflow（Ingestion）· RAGOrchestrator（同步）"]
    D["Pipeline Layer\nStrategy › Pipeline › Stage₁ › … › Stageₙ"]
    E["Provider Layer\nParser / Embedding / LLM / Rerank"]
    F["Infrastructure Layer\nPostgreSQL · Milvus · MinIO · Elasticsearch · Redis · Telemetry"]

    A --> B --> C --> D --> E --> F

    style A fill:#dbeafe,stroke:#3b82f6
    style B fill:#e0f2fe,stroke:#0ea5e9
    style C fill:#f0fdf4,stroke:#22c55e
    style D fill:#fefce8,stroke:#eab308
    style E fill:#fff7ed,stroke:#f97316
    style F fill:#fdf4ff,stroke:#a855f7
```

---

## 核心设计

### 1. Pipeline 框架

Pipeline 由多个 Stage 顺序组成，每个 Stage 只做一件事。Stage 之间通过 `ProcessingContext` 共享租户信息，业务数据通过返回值链式传递。Pipeline 采用不可变构建器模式，`then()` 每次返回新实例。

**Ingestion Pipeline**（Temporal Activity 内运行）：
```
RawFile
  → [ParserStage]       → ParsedDocument
  → [TokenChunkerStage] → list[DocumentChunk]
  → [EmbedStage]        → list[DocumentChunk(with embedding)]
  → [MilvusIndexStage]  → list[DocumentChunk]（写向量）
  → [ESIndexStage]      → list[DocumentChunk]（写全文索引）
```

**Retrieval Pipeline**（单次 HTTP 请求内同步运行）：
```
SearchContext
  → [QueryRewriteStage]  → SearchContext（意图识别 + Multi-Query 扩展，失败时降级）
  → [VectorSearchStage]  → SearchContext（填充 vector_hits）
  → [KeywordSearchStage] → SearchContext（填充 keyword_hits）
  → [RRFFusionStage]     → SearchContext（填充 ranked_hits）
  → [RerankStage]        → list[SearchHit]（Infinity/BGE 精排，不可用时回退 RRF）
```

### 2. Provider 抽象

Stage 描述"做什么"，Provider 描述"怎么做"。通过替换 Provider 切换底层实现，不修改 Stage。

| Provider | 实现 | 用途 |
|---------|------|------|
| `unstructured_parser` | Unstructured 库 | 解析 PDF / Word / Excel / HTML |
| `paddleocr_parser` | PaddleOCR | 扫描件 OCR（置信度 ≥ 0.7） |
| `smart_parser` | Unstructured + MinerU / PaddleOCR | 文档优先快速解析，空文本或扫描件自动降级 |
| `openai_embedding` | OpenAI API | 文本向量化（text-embedding-3-small，1536维） |
| `openai_llm` | OpenAI API | LLM 生成（gpt-4o-mini，流式 + 非流式） |
| `ollama_llm` | Ollama | 本地 LLM（llama3.2，流式） |
| `infinity_rerank` | Infinity + BGE-Reranker-v2-m3 | 检索结果精排，健康检查失败时自动降级 |

### 3. ComponentRegistry

所有 Stage、Provider、Strategy 通过装饰器注册到全局单例。注册在模块 import 时触发，FastAPI `lifespan` 统一导入。

```python
@registry.stage("vector_search")
class VectorSearchStage(BaseStage): ...

@registry.provider("openai_llm")
class OpenAILLMProvider(LLMProvider): ...
```

### 4. Strategy 模式

不同场景对应不同 Pipeline 组合，租户配置中指定 strategy，运行时无需 if-else。

| Strategy | 用途 |
|---------|------|
| `standard` | 标准文档入库（PDF/Word/HTML） |
| `ocr` | 扫描件入库（PaddleOCR 解析） |
| `hybrid` | 混合检索（向量 + BM25 + RRF） |

### 5. Hook 系统

横切能力（配额、幂等性、可观测性、租户隔离）通过 Hook 注入，不侵入 Stage 代码。所有 Ingestion Strategy 已激活 `[TenantGuard, QuotaGuard, IdempotencyGuard, ObservabilityHook]`。

| Hook | Phase | priority | 职责 |
|------|-------|----------|------|
| `TenantGuard` | PRE_PIPELINE | 10 | 校验 tenant_id 格式 |
| `QuotaGuard` | PRE_PIPELINE | 20 | 检查 API / 存储配额 |
| `IdempotencyGuard` | PRE_STAGE + POST_STAGE | 30 | Redis TTL 24h 幂等标记 |
| `ObservabilityHook` | 全 Phase | 100 | OTel Span + Prometheus metrics + 日志 |

### 6. 双链路编排

| 链路 | 编排方式 | 原因 |
|------|---------|------|
| Ingestion（入库） | Temporal Workflow（异步） | 分钟级任务，需断点续跑 |
| RAG 问答（检索+生成） | 同步 HTTP + SSE | 秒级延迟要求，流式输出 |

---

## 存储分工

| 存储 | 存什么 | 何时写入 |
|------|--------|---------|
| **MinIO** | 原始文件（PDF/图片） | 上传时（API 层，Temporal 前） |
| **PostgreSQL** | chunk 文本、元数据、文档状态 | embed_and_index_activity |
| **Milvus** | chunk 向量（1536维，HNSW索引） | embed_and_index_activity |
| **Elasticsearch** | chunk 原文（BM25 倒排索引） | embed_and_index_activity |
| **Redis** | 幂等 key（`idempotency:{task_id}:{stage_name}`，TTL 24h） | IdempotencyGuard POST_STAGE |

---

## 请求链路

### 入库链路

```mermaid
flowchart TD
    Client(["Client"])
    API["POST /documents/upload\nBearer JWT + tenant context"]
    MinIO[("MinIO\n原始文件")]
    SVC["DocumentService\n生成 document_id\n触发 Temporal Workflow"]
    RES["202 Accepted\n{ document_id, task_id }"]
    A1["parse_activity\nUnstructured / PaddleOCR"]
    A2["chunk_activity\nTokenChunkerStage\nsize=512, overlap=64"]
    A3["embed_and_index_activity\nEmbedStage → MilvusIndexStage → ESIndexStage"]
    DB[("PostgreSQL\nMilvus\nElasticsearch")]
    Poll["GET /documents/:id/status"]

    Client --> API --> MinIO --> SVC --> RES --> Client
    SVC -.->|异步| A1 --> A2 --> A3 --> DB
    Client -->|轮询| Poll

    style SVC fill:#f0fdf4
    style A1 fill:#fefce8
    style A2 fill:#fefce8
    style A3 fill:#fefce8
    style DB fill:#fdf4ff
```

### 检索与问答链路

```mermaid
flowchart TD
    Client(["Client"])
    Search["GET /search?q=..."]
    Chat["POST /chat"]
    Orchestrator["RAGOrchestrator"]
    VP["VectorSearchStage\nMilvus ANN"]
    KP["KeywordSearchStage\nES BM25"]
    RRF["RRFFusionStage\nk=60 融合"]
    PG[("PostgreSQL\nget_chunks_by_ids")]
    LLM["LLMProvider\nOpenAI / Ollama"]
    SSE["StreamingResponse\nSSE text/event-stream"]

    Client --> Search --> Orchestrator
    Client --> Chat --> Orchestrator
    Orchestrator --> VP
    Orchestrator --> KP
    VP --> RRF
    KP --> RRF
    RRF --> PG
    PG --> LLM
    LLM --> SSE --> Client

    style Orchestrator fill:#f0fdf4
    style RRF fill:#fefce8
    style SSE fill:#dbeafe
```

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/auth/register`、`/auth/login`、`/auth/refresh`、`/auth/logout` | 本地账号、JWT 与刷新令牌生命周期 |
| `GET/POST/DELETE` | `/documents`、`/documents/upload`、`/documents/{id}` | 文档列表、上传、状态、切片预览、重试与跨存储删除 |
| `GET/POST/DELETE` | `/spaces`、`/spaces/{id}`、`/spaces/{id}/restore` | 知识空间创建、归档与恢复 |
| `GET/POST/PATCH/DELETE` | `/sessions`、`/sessions/{id}` | 会话创建、读取、重命名、删除、归档、恢复与置顶 |
| `GET` | `/sessions/{id}/messages` | 获取消息、附件与 Citation 历史 |
| `POST` | `/chat` | 普通 RAG 问答，SSE 流式输出（含 Citation 事件） |
| `GET/POST/PUT/PATCH` | `/chat/turns`、`/chat/turns/{id}` | 附件轮次创建、上传、补充、重试、忽略、取消与回答 |
| `GET/POST` | `/search`、`/search/debug` | 生产混合检索与逐阶段调试结果 |
| `GET/PUT/POST/DELETE` | `/admin/*` | 真实统计、租户用量与配置、模型定价；开发环境另提供数据重置 |
| `GET` | `/health`、`/ready`、`/metrics` | 存活、就绪与 Prometheus 指标 |

---

## 目录结构

```
arc-knowledge-ai/
├── app/
│   ├── main.py                        # FastAPI 启动，lifespan 组件注册
│   ├── config/settings.py             # Pydantic Settings，读取 .env
│   ├── domain/
│   │   ├── document.py                # RawFile / DocumentChunk / DocumentStatus
│   │   └── retrieval.py               # RetrievalQuery / SearchHit / SearchContext / RetrievalResult
│   ├── pipeline/
│   │   ├── core/                      # context / stage / pipeline / hook / registry
│   │   ├── hooks/                     # tenant_guard / quota_guard / idempotency_guard / observability_hook
│   │   ├── stages/
│   │   │   ├── parsing/               # parser_stage
│   │   │   ├── chunking/              # token_chunker
│   │   │   ├── embedding/             # embed_stage / milvus_index_stage / es_index_stage
│   │   │   └── retrieval/             # query_rewrite / vector_search / keyword_search / rrf_fusion / rerank
│   │   └── strategies/
│   │       ├── ingestion/             # standard_strategy / ocr_strategy
│   │       └── retrieval/             # hybrid_strategy
│   ├── providers/
│   │   ├── base.py                    # EmbeddingProvider / LLMProvider / ParserProvider / RerankProvider
│   │   ├── parser/                    # unstructured_provider / paddleocr_provider
│   │   ├── embedding/                 # openai_embedding
│   │   └── llm/                       # openai_llm / ollama_llm / model_hub / resilient_llm / model_state
│   ├── infrastructure/
│   │   ├── postgres/                  # 连接池 / ChunkRepository
│   │   ├── milvus/                    # 向量写入 / ANN 检索
│   │   ├── minio/                     # 文件上传 / 下载
│   │   ├── elasticsearch/             # BM25 索引 / 全文检索
│   │   ├── redis/                     # 异步连接池（幂等 key 存储）
│   │   └── telemetry/                 # OTel TracerProvider / Prometheus metrics 定义
│   ├── workflows/
│   │   ├── ingestion_workflow.py      # Temporal Workflow 定义
│   │   ├── ingestion_activities.py    # 三个 Activity 实现
│   │   └── rag_orchestrator.py        # RAG 全链路协调器
│   ├── services/
│   │   ├── document_service.py        # 入库业务逻辑
│   │   ├── retrieval_service.py       # 检索业务逻辑
│   │   └── chat_service.py            # 问答业务逻辑
│   └── api/routers/
│       ├── document.py                # /documents 路由
│       ├── search.py                  # /search 路由
│       ├── chat.py                    # /chat 路由（SSE）
│       └── admin.py                   # /admin 路由（模型热切换）
├── scripts/
│   ├── migrate.py                     # PostgreSQL 建表（幂等）
│   └── start_worker.py                # 启动 Temporal Worker
├── tests/
│   └── unit/                          # Pipeline / Stage / Provider 单元测试
├── k8s/                               # K8s 部署配置（namespace / configmap / secret / deployment）
├── docker-compose.yml                 # PostgreSQL / MinIO / Milvus / ES / Redis / Temporal 一键启动
├── notes/                             # 设计文档与学习笔记
└── PROGRESS.md                        # 各阶段进度
```

---

## 版本路线图

| 版本 | 目标 | 状态 |
|------|------|------|
| **v1.0** | Pipeline 框架 + 单文档入库链路 | ✅ 完成 |
| **v2.0** | MinIO 文件存储 + Milvus 向量写入 + OCR 支持 | ✅ 完成 |
| **v3.0** | RAG 检索生成（混合检索 + RRF + LLM 流式） | ✅ 完成 |
| **v4.0** | Hook 系统激活（租户隔离 / 配额 / 幂等 / 可观测性）+ JWT 认证 | ✅ 完成 |
| **v5.0** | OpenTelemetry + Prometheus + Workflow 版本化 + K8s 部署 | ✅ 完成 |
| **v6.0** | 多模型路由（ModelHub + 熔断降级 + Ollama 热切换） | ✅ 完成 |
| **v7.0** | 文档删除（MinIO / Milvus / ES / PG 四处联动） | ✅ 完成 |
| **v8.0–v8.5** | 会话、三层记忆、知识空间基础表与 Citation 持久化 | ✅ 完成 |
| **v9.0** | 检索质量提升（Query Rewrite + Infinity/BGE Rerank） | ✅ 完成 |
| **v10.0** | 核心路径测试补全 | ✅ 完成 |
| **v11.0** | 多租户模型路由重构（三层解析 + TenantConfig 持久化） | ✅ 完成 |
| **v12.0** | 租户限流 | ✅ 完成 |
| **v13.0** | 语义缓存 + 用量与费用追踪 | ✅ 完成 |
| **v14.0** | 可观测性完善（Grafana / Phoenix / 告警）+ Admin UI | ✅ 完成 |
| **v15.x** | 知识空间、扫描 PDF 与智能解析、聊天附件与会话运行时 | ✅ 完成 |
| **下一阶段** | Tenant / Group / Space 细粒度授权 | 📝 设计待审核 |
| **条件触发** | Java 控制面（Gateway / SSO / 组织治理） | ⏸ 暂不启动 |

### 当前收口重点

1. 建立严格类型检查基线并把 `mypy` 纳入 CI。
2. 用固定问题集持续评测检索召回、答案忠实度与相关性。
3. 补充前端到真实后端、真实存储和真实模型的自动化全链路验收。
4. 完成 PostgreSQL、MinIO、Milvus/etcd、Elasticsearch 与 Temporal 的一致性备份及恢复演练。
5. 在真实多人使用前审核并实施 Tenant / Group / Space 权限设计。

---

## 快速启动

```powershell
Copy-Item .env.example .env
./incipit.ps1 doctor
./incipit.ps1 start
./incipit.ps1 smoke -Level L1
```

- Web：`http://127.0.0.1:3300`
- API 文档：`http://127.0.0.1:8000/docs`
- Temporal UI：`http://127.0.0.1:8233`

启动前请按本机模型配置修改 `.env`。状态含义、完整启动、日志和故障恢复见
[本地运行运维手册](./docs/operations/local-runtime.md)。

---

## 设计文档

详细设计见 `notes/` 目录，按顺序阅读：

### Phase 0（v1.0）— Pipeline 框架

| 文档 | 内容 |
|------|------|
| [00-overview](./notes/00-overview.md) | 架构全貌与六个核心抽象 |
| [01-domain-model](./notes/01-domain-model.md) | 领域模型与状态机 |
| [02-context](./notes/02-context.md) | ProcessingContext 设计 |
| [03-stage](./notes/03-stage.md) | BaseStage 与实现 |
| [04-pipeline](./notes/04-pipeline.md) | Pipeline 不可变构建器 |
| [05-hook](./notes/05-hook.md) | Hook 系统与横切能力 |
| [06-registry](./notes/06-registry.md) | ComponentRegistry 单例 |
| [07-provider](./notes/07-provider.md) | Provider 抽象与实现 |
| [08-strategy](./notes/08-strategy.md) | Strategy 模式 |
| [09-workflow](./notes/09-workflow.md) | Temporal Workflow 与 Checkpoint |
| [10-service-api](./notes/10-service-api.md) | Service 层与 API 层 |
| [11-full-flow](./notes/11-full-flow.md) | 完整链路追踪 v1 |
| [12-diagrams](./notes/12-diagrams.md) | 架构图（Mermaid） |

### Phase 1（v2.0）— 完整 Ingestion

| 文档 | 内容 |
|------|------|
| [13-infrastructure](./notes/13-infrastructure.md) | MinIO / Milvus / PG 分工，run_in_executor 设计 |
| [14-full-flow-v2](./notes/14-full-flow-v2.md) | 完整链路追踪 v2（含 MinIO + Milvus） |

### Phase 2（v3.0）— RAG 检索生成

| 文档 | 内容 |
|------|------|
| [15-retrieval-pipeline](./notes/15-retrieval-pipeline.md) | SearchContext、5 个 Retrieval Stage、RRF 算法 |
| [16-full-flow-v3](./notes/16-full-flow-v3.md) | 完整链路追踪 v3（搜索 + RAG 问答 SSE） |
