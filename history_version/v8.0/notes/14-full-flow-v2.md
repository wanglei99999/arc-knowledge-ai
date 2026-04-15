# 14 — 全链路追踪 v2（Phase 1）

> **实现版本**：Phase 1（v2.0）
> Phase 0 的链路见 [11-full-flow.md](./11-full-flow.md)。
> 本文只描述 Phase 1 新增/变更的部分，完整背景请先读 11。

**Phase 1 相比 Phase 0 的变化：**

| 位置 | Phase 0 | Phase 1 |
|------|---------|---------|
| 文件存储 | 路径字符串占位符 | 真实上传到 MinIO |
| document_id 生成 | DocumentService 内部生成 | API 层上传 MinIO 时生成 |
| Activity 3 | 只写 PostgreSQL | 写 PostgreSQL + 写 Milvus |
| Pipeline | Parser → Chunker → Embed | Parser → Chunker → Embed → **MilvusIndex** |

---

## 完整链路（Phase 1）

### 第一阶段：HTTP 层

```
POST /documents/upload
Headers: X-Tenant-Id: tenant-abc
Body:    file=report.pdf, space_id=space-001
```

**变化点 1：API 层生成 document_id 并上传 MinIO**

```python
# api/routers/document.py

document_id = str(uuid.uuid4())          # Phase 1 在此生成
data = await file.read()                 # 读取文件字节

object_key = build_object_key(
    "tenant-abc", "space-001", document_id, "report.pdf"
)
# → "tenant-abc/space-001/doc-111.pdf"

await upload_file(data, object_key, content_type="application/pdf")
# → boto3 → MinIO，run_in_executor 异步包装
```

文件上传完成后，`object_key` 作为 `file_path` 传给 `IngestRequest`。

**为什么在 API 层上传而不是在 Temporal Activity 里？**
Temporal 的 Activity 输入通过事件历史持久化，不适合传递 MB 级二进制数据。文件先落盘 MinIO，Workflow 只传 object_key 字符串。（见 ADR-011）

返回 202：

```json
{ "document_id": "doc-111", "task_id": "task-222" }
```

---

### 第二阶段：Temporal Workflow（同 Phase 0，Activity 3 有变化）

```
IngestionWorkflow
  ├── parse_activity     ← 不变
  ├── chunk_activity     ← 不变
  └── embed_and_index_activity  ← Phase 1 重构
```

---

### 第三阶段：Activity 1 — 解析（变化：file_path 是真实 MinIO key）

```python
raw_file = RawFile(
    file_path="tenant-abc/space-001/doc-111.pdf",   # MinIO object_key
    mime_type="application/pdf",
    original_filename="report.pdf",
)
parser_stage = registry.get_stage("parser")
parsed = await parser_stage.execute(ctx, raw_file)
```

UnstructuredParserProvider 现在收到的是 MinIO object_key。

**注意**：Phase 1 的 `_parse_sync` 直接用这个路径调用 `partition()`，实际上需要文件存在本地。Phase 2 会在解析前先从 MinIO `download_file()` 到临时目录。当前 Phase 1 假设文件路径对 Worker 可访问（本地开发场景下成立）。

---

### 第四阶段：Activity 2 — 切片（不变）

同 Phase 0，Token 滑窗切片，产出 4 个 `DocumentChunk` dict。

---

### 第五阶段：Activity 3 — 向量化 + 双写（Phase 1 核心变化）

**变化点 2：走 Pipeline，embed → milvus_indexer**

```python
# ingestion_activities.py

embed_pipeline = (
    Pipeline.start(registry.get_stage("embedder"))
    .then(registry.get_stage("milvus_indexer"))
)
embedded_chunks = await embed_pipeline.run(ctx, chunks)
```

执行顺序：

**EmbedStage**（不变）
```
4 个 chunk → OpenAI text-embedding-3-small → 4 个 1536 维向量
chunk = dataclasses.replace(chunk, embedding=vector)  ← 不可变更新
```

**MilvusIndexStage**（Phase 1 新增）
```python
records = [
    VectorRecord(
        chunk_id="chunk-0",
        document_id="doc-111",
        tenant_id="tenant-abc",
        chunk_index=0,
        embedding=[0.1, -0.3, ...],   # 1536 维
    ),
    ...  # 共 4 条
]
await insert_vectors(records)
# → pymilvus client.upsert()
# → Milvus Collection: arc_chunk_embeddings
# → Partition: tenant-abc（由 Partition Key 自动路由）
```

**写 PostgreSQL**（同 Phase 0，追加写）
```python
await repo.save_chunks(embedded_chunks)       # 4 个 chunk 含 embedding 元数据
await repo.update_document_status(...)        # → INDEXED
```

**Activity 3 返回**：`4`（写入数量）

---

### 最终状态

```
MinIO（arc-documents bucket）
└── tenant-abc/space-001/doc-111.pdf    ← 原始文件

PostgreSQL（document_chunks 表）
└── 4 行：chunk_id, content, token_count, embedding_model, ...

Milvus（arc_chunk_embeddings collection）
└── 4 条：chunk_id, embedding[1536], tenant_id=tenant-abc（Partition）
```

---

## Phase 1 完整时间线

```
T=0ms      POST /documents/upload
T=200ms    MinIO 上传完成（小文件）
T=250ms    Temporal Workflow 触发
T=300ms    HTTP 202 返回

（异步处理）
T=30s      Activity 1 完成（PDF 解析）
T=32s      Activity 2 完成（切片）
T=92s      EmbedStage 完成（OpenAI）
T=93s      MilvusIndexStage 完成（向量写入）
T=94s      PostgreSQL 写入完成
T=94s      Workflow COMPLETED
```

---

## OCR 场景（Phase 1 新增）

扫描件上传时，传入 `ingestion_strategy=ocr`（由租户配置决定）：

```
POST /documents/upload
Body: file=scan.jpg, space_id=space-001
Header: X-Tenant-Id: tenant-abc

IngestRequest.ingestion_strategy = "ocr"
```

Activity 1 走 `OCRIngestionStrategy`，强制使用 `paddleocr_parser`：

```
PaddleOCRParserProvider._parse_sync()
→ PaddleOCR(use_angle_cls=True, lang="ch")
→ 逐行 OCR，置信度 ≥ 0.7 保留
→ 返回 ParsedDocument(text=..., title=lines[0])
```

后续切片、向量化、写库流程与标准策略完全相同。

---

## 与 Phase 0 的关键差异总结

```
Phase 0 链路：
HTTP → Service → Temporal → parse → chunk → embed → PostgreSQL

Phase 1 链路：
HTTP → MinIO上传 → Service → Temporal → parse → chunk → embed → Milvus + PostgreSQL
  ↑新增                                                      ↑新增
```

Phase 2 会在 Milvus 的基础上加上向量召回，实现完整的 RAG 问答。
