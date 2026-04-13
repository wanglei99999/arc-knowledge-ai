# 09 — Temporal Workflow 与 Activity

**对应代码**：
- `app/workflows/ingestion_workflow.py`
- `app/workflows/ingestion_activities.py`

## 🎯 读完本文你能回答

- Temporal 解决了什么问题？
- Activity 和 Workflow 的区别是什么？
- 为什么 Activity 的输入/输出必须可序列化？
- "Checkpoint 语义"是怎么实现的？

---

## Temporal 解决的问题

文档入库是一个需要几分钟的长耗时任务：

```
解析 PDF（30秒）→ 切片（2秒）→ 调 OpenAI API 向量化（1分钟）→ 写 Milvus（5秒）
```

如果直接写成一个 async 函数，中间任何一步失败都要从头重跑。更严重的是：

- 服务重启 → 任务丢失
- 网络超时 → 无法区分"OpenAI 没收到"还是"收到了但没返回"
- 并发 1000 个文档 → 内存不够，进程挂

**Temporal 的做法**：把长耗时任务拆成多个 **Activity**，每个 Activity 完成后结果持久化到 Temporal Server（基于事件溯源）。失败时只从失败的 Activity 重试，不重跑已完成的步骤。

---

## Workflow vs Activity

```
IngestionWorkflow（Workflow）
    │
    ├── parse_activity（Activity 1）
    ├── chunk_activity（Activity 2）
    └── embed_and_index_activity（Activity 3）
```

| | Workflow | Activity |
|---|---------|---------|
| 职责 | 编排：决定调哪些 Activity、顺序是什么 | 执行：真正做事（调 API、写数据库） |
| 代码限制 | 必须是**确定性的**（不能有随机数、不能直接 IO） | 可以做任何事 |
| 失败处理 | 自动重放历史事件恢复状态 | 失败后重试，从这个 Activity 开始 |
| 超时 | 可以设置总超时 | 每个 Activity 有独立超时 |

**Workflow 代码必须确定性** 这一点很重要：Temporal 在崩溃恢复时会"重放" Workflow 代码，如果相同的输入产生不同的结果（比如用了 `datetime.now()`），重放结果就会和历史不一致。所有有副作用的操作必须在 Activity 里做。

---

## Checkpoint 语义

```python
@workflow.run
async def run(self, inp: IngestionInput) -> dict:
    # Activity 1 完成后，结果 parsed_dict 持久化到 Temporal
    parsed_dict = await workflow.execute_activity(
        parse_activity, inp,
        start_to_close_timeout=timedelta(minutes=10),
        retry_policy=_RETRY,
    )
    # ↑ 如果服务在这里重启，Temporal 知道 parse_activity 已完成
    # 恢复时直接用 parsed_dict，不会再执行 parse_activity

    chunk_dicts = await workflow.execute_activity(
        chunk_activity,
        args=[inp, parsed_dict],
        ...
    )
    # ↑ 同理，chunk_activity 完成后结果也被持久化

    indexed_count = await workflow.execute_activity(
        embed_and_index_activity,
        args=[inp, chunk_dicts],
        ...
    )
```

**第 3 个 Activity 失败时的恢复过程**：

1. Temporal Server 记录：Activity 1 ✅ Activity 2 ✅ Activity 3 ❌
2. Worker 重启后，Temporal 重放历史事件
3. `parse_activity` 和 `chunk_activity` 不再执行，直接用持久化的结果
4. 只有 `embed_and_index_activity` 重试

这就是 **Checkpoint 语义**：每个已完成的 Activity 是一个检查点。

---

## RetryPolicy

```python
_RETRY = RetryPolicy(
    maximum_attempts=3,          # 最多重试 3 次
    initial_interval=timedelta(seconds=5),   # 第一次重试等 5 秒
    backoff_coefficient=2.0,     # 指数退避系数：5s → 10s → 20s
    maximum_interval=timedelta(minutes=5),   # 单次等待最长 5 分钟
)
```

指数退避的效果：

```
第 1 次失败 → 等 5 秒 → 重试
第 2 次失败 → 等 10 秒 → 重试
第 3 次失败 → 等 20 秒 → 重试
第 4 次失败 → 抛出异常，Workflow 失败
```

三个 Activity 用的是同一个 `_RETRY`，但超时时间不同：
- `parse_activity`：10 分钟（大文件解析可能很慢）
- `chunk_activity`：5 分钟（纯计算，理论上很快）
- `embed_and_index_activity`：15 分钟（调 OpenAI + 写 Milvus）

---

## IngestionInput：序列化要求

```python
@dataclass
class IngestionInput:
    tenant_id: str
    document_id: str
    file_path: str
    mime_type: str
    original_filename: str
    task_id: str
    ingestion_strategy: str = "standard"
    embedding_provider: str = "openai_embedding"
    chunk_size: int = 512
    chunk_overlap: int = 64
```

**为什么必须可 JSON 序列化？**

Temporal Server 需要把 Activity 的输入参数存储在事件历史里（用于崩溃恢复）。如果传入不可序列化的对象（如 `ProcessingContext`、`asyncio.Lock`、数据库连接），Temporal 无法存储，会报错。

所以：
- Workflow 和 Activity 的输入/输出：纯数据类型（str, int, dict, list, dataclass）
- `ProcessingContext` 在 Activity **内部**用 `_make_context(inp)` 重建，不跨 Activity 传递

---

## _make_context()：每次重建 Context

```python
def _make_context(inp: IngestionInput) -> ProcessingContext:
    config = TenantConfig(
        tenant_id=inp.tenant_id,
        ingestion_strategy=inp.ingestion_strategy,
        embedding_provider=inp.embedding_provider,
        chunk_size=inp.chunk_size,
        chunk_overlap=inp.chunk_overlap,
    )
    quota = QuotaSnapshot(
        max_documents=999999,
        ...  # Phase 0 无限配额
    )
    return ProcessingContext.create(...)
```

每个 Activity 调用 `_make_context(inp)` 重新构造 Context，而不是把 Context 序列化传给下一个 Activity。

好处：
- 不需要 Context 可序列化
- 每次拿到的是当前配置（避免 Activity 1 的配置快照过时）
- 方便测试（不需要关心跨 Activity 的状态）

`IngestionInput` 里包含了配置快照（`ingestion_strategy`, `chunk_size` 等），保证同一个文档的三个 Activity 用同样的配置。

---

## Activity 内部不使用 Pipeline

注意：三个 Activity 现在是**直接调 Stage**，而不是走 Pipeline：

```python
@activity.defn(name="parse_document")
async def parse_activity(inp: IngestionInput) -> dict:
    ctx = _make_context(inp)
    parser_stage = registry.get_stage("parser")           # 直接取 Stage
    parsed = await parser_stage.execute(ctx, raw_file)
    return {"text": parsed.text, "title": parsed.title, ...}
```

Phase 1 重构时，会改成走 Strategy → Pipeline：

```python
# Phase 1 重构后
strategy = registry.get_strategy(ctx.config.ingestion_strategy)
pipeline = strategy.build_pipeline_with_hooks(inp.mime_type, ctx.config)
result = await pipeline.run(ctx, raw_file)
```

Phase 0 先用最简单的方式跑通流程，Phase 1 再统一架构。

---

## 为什么 Workflow 导入要用 unsafe.imports_passed_through()

```python
with workflow.unsafe.imports_passed_through():
    from app.workflows.ingestion_activities import (
        IngestionInput,
        parse_activity,
        ...
    )
```

Temporal 的沙箱模式会拦截 Workflow 代码里的 import，防止引入不确定性（比如文件系统读取）。但导入 Activity 引用本身是安全的，加 `imports_passed_through()` 告诉 Temporal："这个 import 不是在 Workflow 里执行 IO，只是引用函数名，放行。"

---

## 💡 设计思考

**为什么不把三个 Stage 串成一个 Activity 跑？**

```python
# 方案 A：一个 Activity 跑完
async def ingest_all(inp) -> dict:
    parsed = await parse_stage.execute(...)
    chunks = await chunk_stage.execute(...)
    embedded = await embed_stage.execute(...)
    return result

# 方案 B（当前）：三个 Activity 分开
parsed = await execute_activity(parse_activity, ...)
chunks = await execute_activity(chunk_activity, ..., parsed)
embedded = await execute_activity(embed_and_index_activity, ..., chunks)
```

| | 方案 A | 方案 B（当前） |
|---|-------|-------|
| 颗粒度 | 整体重试（parse 已完成也要重跑） | 单 Activity 重试（从失败点开始） |
| 超时 | 总超时难设（每一步时间不一样） | 每步设独立超时 |
| 监控 | 只知道"整体失败" | 知道哪一步失败、耗时多少 |
| OpenAI 计费 | 失败重跑浪费 token | 向量化结果持久化，不重复计费 |

三个 Activity 的方案成本更低（省 token），可观测性更好。

---

## 下一步

- 了解 Service 和 API 如何触发 Workflow → [10 Service+API](./10-service-api.md)
- 了解完整请求链路 → [11 全链路](./11-full-flow.md)
