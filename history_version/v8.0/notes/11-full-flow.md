# 11 — 全链路追踪：一次文档上传的完整旅程

## 🎯 本文目标

把前面 10 篇学到的所有概念串起来，追踪一次 `POST /documents/upload` 请求从进入到写库的完整执行路径。

---

## 场景

租户 `tenant-abc` 上传一份 5 页 PDF，文件名 `report.pdf`，约 2000 字。

---

## 第一阶段：HTTP 层（毫秒级）

```
客户端：
POST /documents/upload
Headers: X-Tenant-Id: tenant-abc
Body: file=report.pdf, space_id=space-001
```

### 1. FastAPI 接收请求

`app/api/routers/document.py`

```python
async def upload_document(
    file: UploadFile,
    space_id: str,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
) -> UploadResponse:
```

FastAPI 做了什么：
- 解析 multipart/form-data → 得到 `file`（UploadFile 对象）和 `space_id`
- 从 Header 提取 `X-Tenant-Id: tenant-abc` → `x_tenant_id = "tenant-abc"`
- 验证 `file.filename` 非空（`report.pdf` ✅）
- 读取 MIME 类型：`application/pdf`

### 2. 构造 IngestRequest

```python
file_path = "/tenant-abc/space-001/report.pdf"   # Phase 0 占位符

req = IngestRequest(
    tenant_id="tenant-abc",
    space_id="space-001",
    file_path="/tenant-abc/space-001/report.pdf",
    mime_type="application/pdf",
    original_filename="report.pdf",
)
```

### 3. 调用 DocumentService

```python
result = await _service.ingest(req)
# 返回：IngestResult(document_id="doc-111", task_id="task-222", workflow_run_id="run-333")
```

### 4. 返回 202

```json
HTTP/1.1 202 Accepted
{
    "document_id": "doc-111",
    "task_id": "task-222",
    "message": "Document ingestion started"
}
```

**耗时：< 100ms**（Temporal start_workflow 是立即返回的）

---

## 第二阶段：Service 层（毫秒级）

`app/services/document_service.py`

```python
async def ingest(self, req: IngestRequest) -> IngestResult:
    document_id = "doc-111"  # uuid4()
    task_id = "task-222"     # uuid4()

    inp = IngestionInput(
        tenant_id="tenant-abc",
        document_id="doc-111",
        file_path="/tenant-abc/space-001/report.pdf",
        mime_type="application/pdf",
        original_filename="report.pdf",
        task_id="task-222",
        ingestion_strategy="standard",
        embedding_provider="openai_embedding",
        chunk_size=512,
        chunk_overlap=64,
    )

    handle = await client.start_workflow(
        IngestionWorkflow.run,
        inp,
        id="ingest-doc-111",       # Workflow ID
        task_queue="ingestion",
    )
    # Temporal Server 接收到任务，排入队列，立即返回 handle
```

Temporal Server 此时：把 `IngestionInput` 序列化，创建 Workflow 事件历史，Worker 开始执行。

---

## 第三阶段：Temporal Workflow 编排（分钟级）

`app/workflows/ingestion_workflow.py`

Temporal Worker 从 `ingestion` 队列取到任务：

```python
@workflow.run
async def run(self, inp: IngestionInput) -> dict:
    # ---- Activity 1 ----
    parsed_dict = await workflow.execute_activity(
        parse_activity, inp,
        start_to_close_timeout=timedelta(minutes=10),
    )
    # 持久化 parsed_dict 到 Temporal 事件历史
    # ---- Activity 2 ----
    chunk_dicts = await workflow.execute_activity(
        chunk_activity,
        args=[inp, parsed_dict],
        start_to_close_timeout=timedelta(minutes=5),
    )
    # 持久化 chunk_dicts 到 Temporal 事件历史
    # ---- Activity 3 ----
    indexed_count = await workflow.execute_activity(
        embed_and_index_activity,
        args=[inp, chunk_dicts],
        start_to_close_timeout=timedelta(minutes=15),
    )
    return {"document_id": "doc-111", "indexed_chunks": indexed_count, "status": "indexed"}
```

---

## 第四阶段：Activity 1 — 解析（~30秒）

`app/workflows/ingestion_activities.py` → `parse_activity`

### 4.1 重建 ProcessingContext

```python
ctx = _make_context(inp)
# ProcessingContext(
#     tenant_id="tenant-abc",
#     document_id="doc-111",
#     task_id="task-222",
#     trace_id=uuid4(),          # 新生成的追踪 ID
#     config=TenantConfig(
#         ingestion_strategy="standard",
#         embedding_provider="openai_embedding",
#         chunk_size=512,
#         ...
#     ),
#     quota=QuotaSnapshot(max_documents=999999, ...),
#     metadata={},
#     events=[],
# )
```

### 4.2 取 Stage 实例

```python
parser_stage = registry.get_stage("parser")
# registry._stages["parser"] = ParserStage 类
# registry.get_stage("parser") → ParserStage()  ← 每次新建实例
```

### 4.3 ParserStage.execute()

`app/pipeline/stages/parsing/parser_stage.py`

```python
async def execute(self, ctx, raw_file):
    # 前置条件检查（ParserStage.requires = frozenset()，空集，直接通过）
    result = await self._execute(ctx, raw_file)
    # 后置条件检查：ctx.metadata["parsed_title"] 必须存在（produces 声明）
    return result

async def _execute(self, ctx, raw_file):
    provider_id = ctx.config.parser_provider or "unstructured_parser"
    provider = registry.get_provider(provider_id)
    # → UnstructuredParserProvider()

    parsed = await provider.parse(ctx, raw_file.file_path)
    ctx = ctx.with_metadata(parsed_title=parsed.title or "")
    # with_metadata 返回新的 ctx（不可变更新）
    return parsed
```

### 4.4 UnstructuredParserProvider.parse()

`app/providers/parser/unstructured_provider.py`

```python
async def parse(self, ctx, file_path):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, self._parse_sync, file_path)
    # 把阻塞的 partition() 放到线程池执行

def _parse_sync(self, file_path):
    from unstructured.partition.auto import partition   # 懒加载
    elements = partition(filename=file_path)
    # PDF 被解析成 [Title("执行摘要"), Text("本报告..."), Table("..."), ...]

    title = "执行摘要"   # 第一个 Title 元素
    text = "本报告...\n\n表格数据...\n..."

    return ParsedDocument(text=text, title=title)
```

**Activity 1 返回**：

```python
{"text": "本报告...", "title": "执行摘要", "metadata": {}}
```

Temporal 将此结果持久化到事件历史。

---

## 第五阶段：Activity 2 — 切片（~2秒）

`app/workflows/ingestion_activities.py` → `chunk_activity`

### 5.1 重建 Context + 重建 ParsedDocument

```python
ctx = _make_context(inp)    # 再次重建（独立 Activity）
parsed = ParsedDocument(
    text=parsed_dict["text"],
    title=parsed_dict.get("title"),
)
```

### 5.2 TokenChunkerStage._execute()

`app/pipeline/stages/chunking/token_chunker.py`

```python
async def _execute(self, ctx, parsed):
    chunk_size = ctx.config.chunk_size or 512       # 512
    chunk_overlap = ctx.config.chunk_overlap or 64  # 64

    texts = self._split_text(parsed.text, chunk_size, chunk_overlap)
    # 2000字 / 512tokens ≈ 4-5个 chunk

    chunks = []
    for i, text in enumerate(texts):
        chunks.append(DocumentChunk(
            chunk_id=uuid4(),
            document_id="doc-111",
            tenant_id="tenant-abc",
            content=text,
            chunk_index=i,
            token_count=estimate_tokens(text),
        ))
    return chunks   # [chunk_0, chunk_1, chunk_2, chunk_3]
```

**Activity 2 返回**：4 个 chunk 的 dict 列表，每个约 500 tokens。

---

## 第六阶段：Activity 3 — 向量化 + 写库（~60秒）

`app/workflows/ingestion_activities.py` → `embed_and_index_activity`

### 6.1 重建 DocumentChunk 对象

```python
chunks = [DocumentChunk(**d) for d in chunk_dicts]
# 4 个 DocumentChunk，embedding=None
```

### 6.2 EmbedStage._execute()

`app/pipeline/stages/embedding/embed_stage.py`

```python
async def _execute(self, ctx, chunks):
    provider_id = ctx.config.embedding_provider   # "openai_embedding"
    provider = registry.get_provider(provider_id)
    # → OpenAIEmbeddingProvider()

    BATCH_SIZE = 100
    result = []
    for i in range(0, len(chunks), BATCH_SIZE):   # 4 个 chunk，一批搞定
        batch = chunks[i:i+BATCH_SIZE]
        texts = [c.content for c in batch]

        vectors = await provider.embed(ctx, texts)
        # 调用 OpenAI text-embedding-3-small API
        # 返回 4 个向量，每个 1536 维

        for chunk, vec in zip(batch, vectors):
            result.append(dataclasses.replace(chunk, embedding=vec))
            # 不可变更新：返回新的 DocumentChunk，只有 embedding 字段不同

    ctx = ctx.with_metadata(
        embedding_model="text-embedding-3-small",
        embedding_dimension=1536,
    )
    return result   # 4 个带 embedding 的 DocumentChunk
```

### 6.3 OpenAIEmbeddingProvider.embed()

`app/providers/embedding/openai_embedding.py`

```python
async def embed(self, ctx, texts):
    response = await self._client.embeddings.create(
        model="text-embedding-3-small",
        input=texts,    # 4 个文本
    )
    # API 返回：response.data = [
    #   EmbeddingObject(index=0, embedding=[0.1, -0.3, ...]),
    #   EmbeddingObject(index=2, embedding=[0.4, 0.2, ...]),  # ← 顺序不保证
    #   EmbeddingObject(index=1, embedding=[-0.1, 0.5, ...]),
    #   EmbeddingObject(index=3, embedding=[0.7, -0.2, ...]),
    # ]

    sorted_data = sorted(response.data, key=lambda x: x.index)
    # 排序后：[index=0, index=1, index=2, index=3]
    return [item.embedding for item in sorted_data]
    # 4 个 1536 维向量，顺序和输入一致
```

### 6.4 写入 PostgreSQL

```python
repo = ChunkRepository()
await repo.save_chunks(embedded_chunks)
# INSERT INTO document_chunks (...) ON CONFLICT (chunk_id) DO UPDATE ...
# 4 个 chunk 写入，含 embedding 向量

await repo.update_document_status("doc-111", "tenant-abc", DocumentStatus.INDEXED)
# UPDATE documents SET status='indexed' WHERE id='doc-111' AND tenant_id='tenant-abc'
```

**Activity 3 返回**：`4`（写入的 chunk 数量）

---

## 第七阶段：Workflow 完成

Temporal 收到 Activity 3 的返回值，Workflow 完成：

```json
{
    "document_id": "doc-111",
    "indexed_chunks": 4,
    "status": "indexed"
}
```

Temporal Workflow 状态变为 `COMPLETED`。

---

## 客户端轮询

```
GET /documents/doc-111/status
Headers: X-Tenant-Id: tenant-abc

Response (处理中):
{
    "document_id": "doc-111",
    "workflow_status": "RUNNING"
}

Response (完成后):
{
    "document_id": "doc-111",
    "workflow_status": "COMPLETED"
}
```

---

## 完整时间线

```
T=0ms      POST /documents/upload 进入
T=50ms     DocumentService.ingest() 触发 Temporal Workflow
T=100ms    HTTP 202 返回给客户端

（异步处理中...）

T=30s      Activity 1 完成（PDF 解析）
T=32s      Activity 2 完成（文本切片）
T=92s      Activity 3 完成（向量化 + 写库）
T=92s      Workflow 状态 → COMPLETED
```

---

## 失败场景：Activity 3 崩溃

假设 OpenAI API 在第 92s 返回 500 错误：

```
T=0ms    Workflow 启动
T=30s    Activity 1 ✅（parsed_dict 持久化）
T=32s    Activity 2 ✅（chunk_dicts 持久化）
T=92s    Activity 3 ❌（OpenAI 500）
T=97s    Temporal 重试 Activity 3（等 5 秒）
T=157s   Activity 3 ✅（重试成功）
T=157s   Workflow COMPLETED
```

Activity 1 和 Activity 2 **不会重跑**。只有 Activity 3 重试。

---

## 所有文件的角色汇总

| 文件 | 在这次请求中的角色 |
|------|-----------------|
| `api/routers/document.py` | 解析 HTTP 请求，提取 tenant_id，返回 202 |
| `services/document_service.py` | 生成 ID，触发 Temporal Workflow |
| `workflows/ingestion_workflow.py` | 编排三个 Activity，定义重试策略 |
| `workflows/ingestion_activities.py` | 三个 Activity 的实现，重建 Context |
| `pipeline/core/context.py` | 携带整个请求的上下文（租户、配额、配置、事件） |
| `pipeline/core/registry.py` | 按名字取 Stage 和 Provider 实例 |
| `pipeline/stages/parsing/parser_stage.py` | 委托 Provider 解析文件 |
| `providers/parser/unstructured_provider.py` | 实际解析 PDF，run_in_executor 避免阻塞 |
| `pipeline/stages/chunking/token_chunker.py` | 按 token 切片，产出 DocumentChunk 列表 |
| `pipeline/stages/embedding/embed_stage.py` | 批量向量化，不可变更新 chunk |
| `providers/embedding/openai_embedding.py` | 调用 OpenAI embeddings API |
| `infrastructure/postgres/repositories/chunk_repo.py` | 写入 PostgreSQL（upsert） |
| `domain/document.py` | DocumentChunk、DocumentStatus 领域模型 |

---

## 🎓 学习完成

你已经读完了 12 篇文档，掌握了：

1. **领域模型**：DocumentChunk、DocumentStatus 状态机
2. **Context**：不可变更新，事件列表，贯穿全链路
3. **Stage**：泛型 ABC，前置/后置条件检查，业务与框架分离
4. **Pipeline**：不可变构建器，Hook 注入点，SubPipeline 组合
5. **Hook**：横切能力（配额、幂等、可观测性），Phase 枚举，优先级
6. **Registry**：单例，装饰器注册，lifespan 触发
7. **Provider**：AI 能力接口，run_in_executor，Fake 测试
8. **Strategy**：动态 Pipeline 组合，hooks 类变量
9. **Temporal Workflow**：Checkpoint 语义，序列化要求，指数退避
10. **Service + API**：202 响应，X-Tenant-Id，lifespan 注册

**下一步**：
- Phase 1：接入 MinIO 存储真实文件，接入 Milvus 写向量
- Phase 2：实现 RAG 检索（向量召回 + BM25 + 重排序）
- Phase 3：激活 Hook 系统（TenantGuard, QuotaGuard, IdempotencyGuard, ObservabilityHook）

参见 `PROGRESS.md` 了解各阶段任务清单。
