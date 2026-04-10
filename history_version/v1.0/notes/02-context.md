# 02 — ProcessingContext

**对应代码**：`app/pipeline/core/context.py`

## 🎯 读完本文你能回答

- Context 是什么，为什么要有它？
- `with_metadata()` 为什么返回新对象而不是修改原对象？
- Stage 之间怎么传递中间结果？

---

## Context 是什么

每个请求进来，系统创建一个 `ProcessingContext`，它像"快递单"一样随着请求流经每个 Stage：

```python
@dataclass
class ProcessingContext:
    tenant_id: str       # 这个请求属于哪个租户（安全边界）
    document_id: str     # 处理的是哪个文档
    task_id: str         # Temporal Workflow ID（用于追踪）
    trace_id: str        # OpenTelemetry Trace ID（用于链路追踪）
    quota: QuotaSnapshot # 这个租户的配额快照
    config: TenantConfig # 租户配置（用哪个 provider、chunk 多大等）
    metadata: dict       # Stage 间传递中间结果的扩展区
    events: list         # 本次请求产生的领域事件（结束时统一广播）
```

---

## 为什么需要 Context

**没有 Context 的写法**（函数参数传递）：

```python
async def parse(tenant_id, document_id, trace_id, config, file_path): ...
async def chunk(tenant_id, document_id, trace_id, config, text, title): ...
async def embed(tenant_id, document_id, trace_id, config, chunks): ...
```

每个函数都要重复传 `tenant_id`、`trace_id`、`config`。
加一个字段（比如 `quota`）要改所有函数签名。

**有 Context 的写法**：

```python
async def parse(ctx: ProcessingContext, file_path: str): ...
async def chunk(ctx: ProcessingContext, text: str): ...
async def embed(ctx: ProcessingContext, chunks: list): ...
```

所有公共信息都在 `ctx` 里，新增字段只改 Context 定义，函数签名不变。

---

## metadata：Stage 间的"便条"

```python
metadata: dict[str, Any] = field(default_factory=dict)
```

Stage 执行完，可以往 metadata 写东西，供后续 Stage 读取：

```python
# ParserStage 执行后，把标题写进 context
if result.title:
    ctx.metadata["parsed_title"] = result.title

# EmbedStage 执行后，写入模型信息供 MilvusIndexStage 使用
ctx.metadata["embedding_model"] = provider.get_model_name()
ctx.metadata["embedding_dimension"] = provider.get_dimension()
```

这就是 `BaseStage` 里的 `produces` 字段的含义：

```python
class EmbedStage(BaseStage):
    produces = frozenset({"embedding_model", "embedding_dimension"})
    # 声明"我执行后会往 context 写这两个 key"
```

---

## 不可变更新

```python
def with_metadata(self, **kwargs) -> "ProcessingContext":
    """不可变更新：返回新 Context，原 Context 不变"""
    return dataclasses.replace(self, metadata={**self.metadata, **kwargs})
```

`dataclasses.replace()` 创建一个新对象，字段值复制过去，只有指定的字段用新值。

**为什么不直接 `ctx.metadata["key"] = value`？**

因为 Stage 不应该修改传进来的 Context。如果两个并发 Stage 同时修改同一个 Context，会发生竞争条件。返回新 Context 是并发安全的。

实际上在 Phase 0 的顺序 Pipeline 里，直接修改也不会出问题。但养成不可变更新的习惯，Phase 2 做并行检索时就不会踩坑。

---

## TenantConfig：租户差异化配置

```python
@dataclass
class TenantConfig:
    tenant_id: str
    ingestion_strategy: str = "standard"    # 用哪套处理方案
    embedding_provider: str = "openai_embedding"  # 用哪个嵌入模型
    chunk_size: int = 512                   # 切片大小
    chunk_overlap: int = 64                 # 重叠 token 数
    top_k: int = 10                         # 检索返回条数
    rerank_enabled: bool = True             # 是否开启重排序
```

不同租户可以有不同配置。免费租户 `chunk_size=256`，付费租户 `chunk_size=1024`。
Stage 从 `ctx.config` 读配置，不需要知道"这是什么租户"。

---

## QuotaSnapshot：配额快照

```python
@dataclass
class QuotaSnapshot:
    max_documents: int
    max_api_calls_per_day: int
    used_api_calls_today: int
    ...

    def has_api_quota(self) -> bool:
        return self.used_api_calls_today < self.max_api_calls_per_day
```

在请求开始时创建配额**快照**，而不是每次 Stage 都去查数据库。

原因：一次文档处理可能调用 OpenAI 几十次（每批 100 个 chunk），如果每次都查数据库，既慢又可能读到不一致的数据。

Phase 3 的 `QuotaGuard` Hook 会在 Pipeline 结束后统一扣减实际消耗。

---

## 创建方式

```python
# 标准创建（task_id 和 trace_id 不传则自动生成 uuid）
ctx = ProcessingContext.create(
    tenant_id="tenant-abc",
    document_id="doc-001",
    quota=quota_snapshot,
    config=tenant_config,
)

# 测试时创建（conftest.py 里的 fake_ctx）
ctx = ProcessingContext.create(
    tenant_id="test-tenant",
    document_id="doc-001",
    quota=QuotaSnapshot(max_documents=999999, ...),
    config=TenantConfig(tenant_id="test-tenant"),
)
```

---

## 💡 设计思考

**为什么 Context 里有 `events` 列表，而不是每个 Stage 直接发事件？**

如果 Stage 直接调 `event_bus.publish(event)`，当 Stage 在事务里出错回滚时，事件已经发出去了（脏事件）。

把事件暂存在 `ctx.events` 里，Pipeline 成功完成后再统一广播，就不会有脏事件。这是经典的"事务性发件箱"模式（Transactional Outbox）。

---

## 下一步

- 了解 Stage 如何使用 Context → [03 Stage](./03-stage.md)
- 了解 Pipeline 怎么传递 Context → [04 Pipeline](./04-pipeline.md)
