# 12 — v1.0 架构全图（Mermaid）

快速回顾用，四张图从不同视角描述同一套系统。

---

## 图一：六层架构总览

```mermaid
graph TD
    subgraph L1["① Interface Layer"]
        API["FastAPI Router\n/documents/upload\n/documents/:id/status"]
    end

    subgraph L2["② Application Layer"]
        SVC["DocumentService\n生成ID，触发Workflow"]
    end

    subgraph L3["③ Orchestration Layer"]
        WF["IngestionWorkflow\n编排三个Activity"]
        A1["parse_activity"]
        A2["chunk_activity"]
        A3["embed_and_index_activity"]
        WF --> A1 --> A2 --> A3
    end

    subgraph L4["④ Pipeline Layer"]
        STG["StandardIngestionStrategy"]
        PL["Pipeline\nrun + HookRunner"]
        S1["ParserStage"]
        S2["TokenChunkerStage"]
        S3["EmbedStage"]
        STG --> PL
        PL --> S1 --> S2 --> S3
    end

    subgraph L5["⑤ Provider Layer"]
        P1["UnstructuredParserProvider\nrun_in_executor"]
        P2["OpenAIEmbeddingProvider\nsorted by index"]
    end

    subgraph L6["⑥ Infrastructure Layer"]
        PG[("PostgreSQL\ndocument_chunks")]
        MV[("Milvus\n向量库 Phase1")]
        MN[("MinIO\n文件存储 Phase1")]
    end

    API --> SVC --> WF
    A1 --> S1
    A2 --> S2
    A3 --> S3
    S1 --> P1
    S3 --> P2
    A3 --> PG

    style L1 fill:#dbeafe
    style L2 fill:#e0f2fe
    style L3 fill:#f0fdf4
    style L4 fill:#fefce8
    style L5 fill:#fff7ed
    style L6 fill:#fdf4ff
```

---

## 图二：核心组件关系图

```mermaid
graph LR
    subgraph Registry["ComponentRegistry（单例）"]
        RS["_stages\n{ parser, token_chunker, embedder }"]
        RP["_providers\n{ unstructured_parser, openai_embedding }"]
        RT["_strategies\n{ standard }"]
    end

    subgraph Strategy["Strategy"]
        BS["BaseStrategy\nhooks: ClassVar[list]"]
        SS["StandardIngestionStrategy\nhooks = []"]
        BS --> SS
    end

    subgraph Pipeline["Pipeline（不可变构建器）"]
        PL["Pipeline\n.start() .then() .with_hooks()"]
        HR["HookRunner\n按 priority 排序执行"]
        PL --> HR
    end

    subgraph Stages["Stage（处理单元）"]
        PS["ParserStage\nproduces: parsed_title"]
        TC["TokenChunkerStage\nproduces: chunk_count"]
        ES["EmbedStage\nproduces: embedding_dimension"]
    end

    subgraph Providers["Provider（可替换实现）"]
        UP["UnstructuredParserProvider"]
        OE["OpenAIEmbeddingProvider"]
    end

    CTX["ProcessingContext\ntenant_id / document_id\nconfig / quota / metadata / events"]

    SS -->|build_pipeline| PL
    PL -->|顺序执行| PS
    PL -->|顺序执行| TC
    PL -->|顺序执行| ES

    PS -->|registry.get_provider| UP
    ES -->|registry.get_provider| OE

    SS -->|registry 注册| RT
    PS -->|registry 注册| RS
    UP -->|registry 注册| RP

    CTX -.->|ctx 参数贯穿| PS
    CTX -.->|ctx 参数贯穿| TC
    CTX -.->|ctx 参数贯穿| ES

    style CTX fill:#fef9c3,stroke:#ca8a04
    style Registry fill:#f1f5f9
```

---

## 图三：一次请求的时序图

```mermaid
sequenceDiagram
    actor Client
    participant API as FastAPI Router
    participant SVC as DocumentService
    participant TMP as Temporal Server
    participant WF as IngestionWorkflow
    participant A1 as parse_activity
    participant A2 as chunk_activity
    participant A3 as embed_and_index_activity
    participant OAI as OpenAI API
    participant DB as PostgreSQL

    Client->>API: POST /documents/upload\nX-Tenant-Id: tenant-abc
    API->>SVC: ingest(IngestRequest)
    SVC->>TMP: start_workflow(IngestionInput)
    TMP-->>SVC: workflow handle（立即返回）
    SVC-->>API: IngestResult(doc_id, task_id)
    API-->>Client: 202 Accepted { document_id, task_id }

    Note over TMP,A3: 异步执行（分钟级）

    TMP->>WF: 调度 Workflow
    WF->>A1: execute_activity(parse)
    A1->>A1: Unstructured 解析 PDF
    A1-->>TMP: parsed_dict ✅ 持久化

    WF->>A2: execute_activity(chunk, parsed_dict)
    A2->>A2: Token 切片
    A2-->>TMP: chunk_dicts ✅ 持久化

    WF->>A3: execute_activity(embed_and_index, chunk_dicts)
    A3->>OAI: embeddings.create(texts)
    OAI-->>A3: vectors（sorted by index）
    A3->>DB: save_chunks (upsert)
    A3->>DB: update_document_status → INDEXED
    A3-->>TMP: indexed_count ✅

    WF-->>TMP: COMPLETED

    Client->>API: GET /documents/{id}/status
    API-->>Client: { workflow_status: "COMPLETED" }
```

---

## 图四：Temporal Checkpoint 重试机制

```mermaid
stateDiagram-v2
    [*] --> Activity1_Running : Workflow 启动

    Activity1_Running --> Activity1_Done : 成功
    Activity1_Running --> Activity1_Retry : 失败
    Activity1_Retry --> Activity1_Running : 重试（指数退避 5s→10s→20s）
    Activity1_Retry --> WorkflowFailed : 超过 3 次

    Activity1_Done --> Activity2_Running : 结果持久化 ✅
    Activity2_Running --> Activity2_Done : 成功
    Activity2_Running --> Activity2_Retry : 失败
    Activity2_Retry --> Activity2_Running : 重试
    Activity2_Retry --> WorkflowFailed : 超过 3 次

    Activity2_Done --> Activity3_Running : 结果持久化 ✅
    Activity3_Running --> Activity3_Done : 成功
    Activity3_Running --> Activity3_Retry : 失败（如 OpenAI 500）
    Activity3_Retry --> Activity3_Running : 只重试 Activity3\nActivity1/2 不重跑
    Activity3_Retry --> WorkflowFailed : 超过 3 次

    Activity3_Done --> [*] : COMPLETED ✅
    WorkflowFailed --> [*] : FAILED ❌

    note right of Activity1_Done : parsed_dict 存入\nTemporal 事件历史
    note right of Activity2_Done : chunk_dicts 存入\nTemporal 事件历史
```

---

## 图五：Hook 执行时序（Phase 3 激活后）

```mermaid
sequenceDiagram
    participant PL as Pipeline.run()
    participant HR as HookRunner
    participant TG as TenantGuard\npriority=10
    participant QG as QuotaGuard\npriority=20
    participant IG as IdempotencyGuard\npriority=30
    participant OB as ObservabilityHook\npriority=100
    participant ST as Stage._execute()

    PL->>HR: fire(PRE_PIPELINE)
    HR->>TG: handle() → 验证租户，注入RLS
    TG-->>HR: CONTINUE

    loop 每个 Stage
        PL->>HR: fire(PRE_STAGE)
        HR->>TG: handle() → （不处理）
        HR->>QG: handle() → 检查配额
        QG-->>HR: CONTINUE / ABORT
        HR->>IG: handle() → 检查是否已处理
        IG-->>HR: CONTINUE / SKIP_STAGE
        HR->>OB: handle() → 开始计时 Span
        OB-->>HR: CONTINUE

        alt SKIP_STAGE
            PL->>PL: continue（跳过此Stage）
        else CONTINUE
            PL->>ST: _execute(ctx, input)
            ST-->>PL: output

            PL->>HR: fire(POST_STAGE)
            HR->>IG: handle() → 写 Redis 完成标记
            HR->>OB: handle() → 结束计时，记录 metrics
        end
    end

    PL->>HR: fire(POST_PIPELINE)
    HR->>QG: handle() → 扣减实际消耗
```

---

## 一张图记住核心关系

```
HTTP请求 → [API] → [Service] → [Temporal] → [Activities]
                                                   │
                                            每个Activity内：
                                            _make_context(inp) → ctx
                                            registry.get_stage() → Stage
                                            Stage._execute(ctx, input)
                                                   │
                                            Stage内：
                                            registry.get_provider() → Provider
                                            provider.do_work(ctx, input)
                                                   │
                                            返回结果 → 下一个Activity
```

**ctx 是唯一贯穿所有层的对象，但不跨 Activity 传递（每次重建）。**
