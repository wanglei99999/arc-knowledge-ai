# 10 — Service 层与 API 层

**对应代码**：
- `app/services/document_service.py`
- `app/api/routers/document.py`
- `app/main.py`

## 🎯 读完本文你能回答

- Service 层的职责是什么？
- API 为什么返回 202 而不是 200？
- `X-Tenant-Id` Header 是怎么传进来的？
- `lifespan` 和 `_register_components()` 的关系是什么？

---

## 整体分层

```
HTTP 请求
    │
    ▼
[API 层] document.py
    │  解析请求参数，验证格式，调用 Service
    ▼
[Service 层] document_service.py
    │  业务逻辑：生成 ID，触发 Temporal Workflow
    ▼
[Temporal] IngestionWorkflow
    │  编排三个 Activity
    ▼
[Pipeline] Stage 执行链
```

每一层只和相邻层交互，API 层不直接操作 Temporal，Service 层不知道 HTTP 细节。

---

## DocumentService 解析

```python
class DocumentService:
    async def ingest(self, req: IngestRequest) -> IngestResult:
        document_id = str(uuid.uuid4())   # 生成唯一 ID
        task_id = str(uuid.uuid4())       # 生成任务 ID（用于轮询）

        inp = IngestionInput(
            tenant_id=req.tenant_id,
            document_id=document_id,
            ...
        )

        client = await self._get_temporal_client()
        handle = await client.start_workflow(
            IngestionWorkflow.run,
            inp,
            id=f"ingest-{document_id}",        # Workflow ID 用文档 ID，方便后续查询
            task_queue=settings.temporal_task_queue,
        )

        return IngestResult(
            document_id=document_id,
            task_id=task_id,
            workflow_run_id=handle.run_id,
        )
```

**Service 做了什么**：
1. 生成两个 UUID（`document_id` 用于标识文档，`task_id` 用于轮询）
2. 把 `IngestRequest` 转换成 `IngestionInput`（Service 对外接受业务请求，对内构造 Temporal 输入）
3. 调用 Temporal `start_workflow`，**立即返回**（不等待完成）

**为什么 Workflow ID 是 `ingest-{document_id}`？**

Temporal 的 Workflow ID 是唯一的。如果用相同 ID 再次提交，Temporal 会返回已有 Workflow 的 handle。这提供了**天然的幂等性**：同一个文档不会触发两个 Workflow。

---

## get_status()：查询 Workflow 状态

```python
async def get_status(self, document_id: str) -> dict:
    client = await self._get_temporal_client()
    handle = client.get_workflow_handle(f"ingest-{document_id}")
    desc = await handle.describe()
    return {
        "document_id": document_id,
        "workflow_status": desc.status.name,
        # → "RUNNING" | "COMPLETED" | "FAILED" | "TERMINATED" | "TIMED_OUT"
    }
```

前端轮询这个接口，状态从 `RUNNING` 变成 `COMPLETED` 或 `FAILED`。

---

## API 层解析

```python
@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,   # 202，不是 200
)
async def upload_document(
    file: UploadFile,
    space_id: str,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),  # 从 Header 读租户 ID
) -> UploadResponse:
```

**为什么是 202 而不是 200？**

- `200 OK`：请求已完成，响应体就是最终结果
- `202 Accepted`：请求已接受，但处理**尚未完成**（异步处理中）

文档入库需要几分钟，不能让 HTTP 连接等几分钟。202 告诉客户端："我收到了你的请求，已经开始处理，请用 `GET /documents/{id}/status` 轮询结果。"

---

## X-Tenant-Id：多租户隔离的起点

```python
x_tenant_id: str = Header(..., alias="X-Tenant-Id")
```

FastAPI 自动从 HTTP Header `X-Tenant-Id` 读取租户 ID，`...` 表示必填（缺少则返回 422）。

这个值会：
1. 传给 `IngestRequest.tenant_id`
2. 进入 `ProcessingContext.tenant_id`
3. 在 Phase 3 被 `TenantGuard` 验证（存在且未被禁用）
4. 在数据库操作时作为 RLS 过滤条件

整条链路里的租户隔离，从这个 Header 开始。

---

## 文件路径：Phase 0 的占位符

```python
# Phase 0：直接用路径占位
file_path = f"/{x_tenant_id}/{space_id}/{file.filename}"

# Phase 1 会改成：
content = await file.read()
minio_path = await minio_client.upload(
    bucket="documents",
    key=f"{x_tenant_id}/{space_id}/{document_id}",
    data=content,
    content_type=mime_type,
)
file_path = minio_path
```

Phase 0 跳过了实际的文件上传，用路径字符串作为占位。这样整个流程可以跑通，Phase 1 只需要补充 MinIO 上传逻辑。

---

## main.py：FastAPI 启动

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    _register_components()   # 启动时注册所有组件
    yield                    # 应用运行
    await dispose()          # 关闭时清理资源


app = FastAPI(title="ArcKnowledge Data Plane", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],     # Phase 1 收紧
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(document_router)
```

### lifespan 的执行流程

```
uvicorn 启动
    │
    ▼
lifespan 进入（yield 之前）
    │
    ├── _register_components()
    │       ├── import token_chunker  → @registry.stage("token_chunker") 执行
    │       ├── import embed_stage    → @registry.stage("embedder") 执行
    │       ├── import parser_stage   → @registry.stage("parser") 执行
    │       ├── import standard_strategy → @registry.strategy("standard") 执行
    │       ├── import openai_embedding  → @registry.provider("openai_embedding") 执行
    │       └── import unstructured_provider → @registry.provider("unstructured_parser") 执行
    │
    ▼
yield（应用就绪，开始接受请求）
    │
    ▼
Ctrl+C / 服务关闭
    │
    ▼
lifespan 继续（yield 之后）
    │
    └── dispose() → 关闭 PG 连接池
```

`_register_components()` 是必须的，因为 Python 不会自动 import 所有模块（只有被引用到的才会 import）。没有这一步，运行时会 `StageNotFoundError: Stage 'parser' not registered`。

---

## 完整 Service 调用链

```
POST /documents/upload
    │
    ├── FastAPI 解析 multipart/form-data → file, space_id
    ├── FastAPI 读取 Header X-Tenant-Id → x_tenant_id
    ├── 验证 file.filename 非空
    │
    ▼
DocumentService.ingest(IngestRequest)
    ├── document_id = uuid4()
    ├── task_id = uuid4()
    ├── 构造 IngestionInput（把业务请求翻译成 Temporal 输入）
    │
    ▼
Temporal Client.start_workflow(IngestionWorkflow.run, inp, id=f"ingest-{document_id}")
    │
    └── 立即返回 handle（不等待 Workflow 完成）
    │
    ▼
返回 HTTP 202 UploadResponse
{
    "document_id": "xxx-yyy-zzz",
    "task_id": "aaa-bbb-ccc",
    "message": "Document ingestion started"
}
```

---

## 💡 设计思考

**Service 层为什么不直接做业务，而是触发 Temporal？**

如果 DocumentService.ingest() 直接 await 整个处理流程：

```python
# 如果不用 Temporal
async def ingest(self, req):
    parsed = await parser_stage.execute(...)   # 30秒
    chunks = await chunker_stage.execute(...)  # 2秒
    embedded = await embed_stage.execute(...)  # 60秒
    # HTTP 连接一直等待...
```

问题：
- HTTP 连接保持 90 秒，客户端可能超时
- FastAPI 进程挂掉 → 任务丢失
- 无法重试（不知道哪步失败了）

用 Temporal 之后：
- HTTP 立即返回（202）
- 任务状态由 Temporal Server 持久化
- 任意步骤可独立重试

**Service 层的边界**：接受业务请求 → 生成 ID → 触发异步任务 → 返回任务句柄。业务逻辑（怎么处理文档）在 Pipeline/Stage 里，不在 Service 里。

---

## 下一步

- 看完整的请求链路从头到尾 → [11 全链路](./11-full-flow.md)
