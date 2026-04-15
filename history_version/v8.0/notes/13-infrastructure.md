# 13 — 基础设施层：MinIO + Milvus + PostgreSQL

> **实现版本**：Phase 1（v2.0）

**对应代码**：
- `app/infrastructure/minio/client.py`
- `app/infrastructure/milvus/client.py`
- `app/infrastructure/postgres/client.py`
- `app/infrastructure/postgres/repositories/chunk_repo.py`

## 读完本文你能回答

- 为什么基础设施层全部用 `run_in_executor`？
- PostgreSQL 和 Milvus 各存什么，为什么要拆开？
- MinIO 的 object_key 格式是什么，为什么这样设计？
- Milvus 怎么实现多租户隔离？

---

## 基础设施层的职责

基础设施层是系统与外部存储的唯一接触点，上层（Stage / Repository）不直接操作数据库连接或 SDK。

```
Stage / Repository
       │
       ▼
  infrastructure/     ← 这一层
  ├── minio/          封装 boto3
  ├── milvus/         封装 pymilvus
  └── postgres/       封装 SQLAlchemy
       │
       ▼
  MinIO / Milvus / PostgreSQL（外部进程）
```

---

## 为什么全部用 run_in_executor？

三个存储的 Python SDK 都是**同步库**：

| 库 | 同步原因 |
|----|---------|
| `boto3` | AWS 官方 SDK，只有同步版本 |
| `pymilvus` | 无原生 async 支持 |
| `asyncpg` | 原生异步（例外） |

FastAPI 和 Temporal Worker 都运行在 asyncio 事件循环上。如果直接在 `async def` 里调用同步 IO，会阻塞整个事件循环，导致其他请求全部等待。

`run_in_executor` 把同步调用放到线程池执行：

```python
loop = asyncio.get_event_loop()
result = await loop.run_in_executor(None, sync_function, arg1, arg2)
#                                   ↑
#                              None = 使用默认线程池
```

事件循环继续处理其他协程，同步函数在独立线程里跑，完成后把结果交回事件循环。

---

## 三库各存什么：Split Storage 设计

| 存储 | 存什么 | 为什么 |
|------|--------|--------|
| **PostgreSQL** | 文档元数据、chunk 文本内容、状态、token 数 | 关系型查询（按文档/状态筛选），事务保证 |
| **Milvus** | chunk 的向量（embedding） | 专门为 ANN 向量检索优化，PG 不擅长 |
| **MinIO** | 原始文件（PDF / Word / 图片） | 对象存储，文件不应存在关系型数据库 |

检索时的两步流程：

```
查询向量 → Milvus ANN 检索 → [chunk_id 列表]
                                    │
                                    ▼
                           PostgreSQL 按 chunk_id 查文本内容
```

向量库只返回 ID，内容还是从 PG 读——这样 Milvus 的存储量小，检索快。

---

## MinIO：object_key 格式

```python
def build_object_key(tenant_id, space_id, document_id, filename):
    suffix = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
    return f"{tenant_id}/{space_id}/{document_id}.{suffix}"

# 示例：
# "tenant-abc/space-001/doc-111.pdf"
```

**为什么用 document_id 而不是原始文件名？**

- 文件名可能重复（两个用户都上传 `report.pdf`）
- 文件名可能含特殊字符，在 URL 里需要转义
- `document_id` 是 UUID，全局唯一，无冲突

**为什么路径包含 tenant_id 和 space_id？**

- MinIO 的 bucket 是全局共享的（`arc-documents`）
- 路径前缀相当于虚拟目录，便于按租户/空间批量操作
- 权限策略可以按路径前缀设置（Phase 4 收紧 MinIO 访问控制）

---

## Milvus：Partition Key 多租户隔离

Milvus 的 Collection 是所有租户共享的（`arc_chunk_embeddings`），用 `tenant_id` 字段作为 **Partition Key** 实现物理隔离：

```python
schema.add_field(
    FIELD_TENANT_ID,
    DataType.VARCHAR,
    max_length=64,
    is_partition_key=True,   # ← 关键
)
```

检索时加 filter，只在当前租户的 Partition 里搜：

```python
client.search(
    collection_name=COLLECTION_NAME,
    data=[query_vector],
    filter=f'tenant_id == "{tenant_id}"',   # ← 租户隔离
    limit=top_k,
)
```

**Partition Key vs 每租户一个 Collection：**

| | Partition Key（当前） | 每租户一个 Collection |
|---|---|---|
| 管理复杂度 | 低（一个 Collection） | 高（租户多时 Collection 爆炸） |
| 隔离级别 | 逻辑隔离（同一物理存储） | 物理隔离 |
| 新增租户 | 无需操作 | 需要创建新 Collection |
| 适用规模 | 中小规模（< 1000 租户） | 大规模 SaaS |

---

## Milvus：upsert vs insert

`insert_vectors()` 使用 `client.upsert()`，而不是 `client.insert()`：

```python
client.upsert(collection_name=COLLECTION_NAME, data=data)
```

原因：Temporal Activity 3 有重试机制。如果 embed 成功但写 Milvus 失败，Activity 重试时会再次写入相同的 chunk_id。`upsert` 保证重复写入是安全的（覆盖而不是报错）。

---

## PostgreSQL：连接池

```python
engine = create_async_engine(
    settings.postgres_url,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,   # 每次取连接前 ping 一下，避免使用已断开的连接
)
```

`get_session()` 是 async context manager：

```python
async with get_session() as session:
    await session.execute(...)
    # 正常退出 → commit
    # 异常 → rollback
```

Repository 层不直接持有 session，每次操作通过 `get_session()` 取，用完自动归还连接池。

---

## 下一步

- 了解 Milvus 向量检索在 Phase 2 如何用于 RAG → （Phase 2 后补充）
- 了解完整的 Phase 1 请求链路 → [14-full-flow-v2](./14-full-flow-v2.md)
