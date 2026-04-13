# 01 — 领域模型

**对应代码**：`app/domain/document.py`

## 🎯 读完本文你能回答

- 系统里的核心数据对象是什么？
- `DocumentStatus` 状态机是什么，为什么要用状态机？
- `RawFile`、`ParsedDocument`、`DocumentChunk` 三者的区别？

---

## 为什么要单独有 domain 层

`domain/` 是系统的最底层，**只有纯 Python 数据类，没有任何 I/O**。

好处：
1. 任何层都可以 import domain，不会产生循环依赖
2. 单元测试不需要数据库、不需要网络，直接 new 一个对象
3. 数据结构的变更集中在一个地方

---

## 三个核心数据类

### RawFile — 入口数据

```python
@dataclass
class RawFile:
    file_path: str           # MinIO 或本地路径
    mime_type: str           # "application/pdf"
    original_filename: str
    size_bytes: int = 0
```

这是 Pipeline 的**起点**。用户上传的文件，在被处理之前就是一个 `RawFile`。

---

### DocumentChunk — 核心单元

```python
@dataclass
class DocumentChunk:
    document_id: str         # 属于哪个文档
    tenant_id: str           # 属于哪个租户（多租户隔离）
    content: str             # 文本内容
    chunk_index: int         # 第几个切片（保证顺序）
    token_count: int = 0     # 估算 token 数
    chunk_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict = ...     # 扩展字段（来源标题、页码等）
    embedding: list[float] | None = None   # EmbedStage 执行后填充
```

`DocumentChunk` 是整个系统最重要的数据单元：
- 切片 Stage 生产它
- Embedding Stage 给它填充向量
- 检索时按向量相似度找到它
- RAG 生成时把它的 content 塞进 prompt

`embedding` 字段初始为 `None`，由 `EmbedStage` 执行后填充，这是**流水线数据流**的体现。

---

### DocumentStatus — 状态机

```python
class DocumentStatus(Enum):
    PENDING   = "pending"    # 等待处理
    PARSING   = "parsing"    # 解析中
    PARSED    = "parsed"     # 解析完成
    CHUNKING  = "chunking"   # 切片中
    CHUNKED   = "chunked"
    EMBEDDING = "embedding"  # 向量化中
    INDEXED   = "indexed"    # 已入库，可被检索
    FAILED    = "failed"     # 失败
    STALE     = "stale"      # 索引过期，需要 reindex
```

合法转换表：

```python
VALID_TRANSITIONS = {
    PENDING:   {PARSING, FAILED},
    PARSING:   {PARSED,  FAILED},
    PARSED:    {CHUNKING, FAILED},
    ...
    INDEXED:   {STALE},           # 文档更新后变 Stale
    STALE:     {PARSING},         # 重新处理
    FAILED:    {PARSING},         # 重试
}
```

**为什么要状态机？**

玩具系统用一个 `is_done: bool` 字段，工业系统用状态机：

| 问题 | bool 字段 | 状态机 |
|------|-----------|-------|
| 崩溃恢复 | 不知道挂在哪步 | 从最后一个状态恢复 |
| 进度展示 | 只能显示完成/未完成 | 显示"解析中 40%" |
| 重试逻辑 | 全部重跑 | 从失败的那步重试 |
| 非法操作 | 随时可以任意修改 | INDEXED → PARSING 被禁止 |

---

## 数据在 Pipeline 里的变形

```
RawFile
   │  (ParserStage)
   ▼
ParsedDocument          ← 定义在 providers/base.py（Provider 的返回类型）
   │  (TokenChunkerStage)
   ▼
list[DocumentChunk]     ← embedding=None
   │  (EmbedStage)
   ▼
list[DocumentChunk]     ← embedding=[0.1, 0.2, ...]（已填充）
   │  (ChunkRepository.save_chunks)
   ▼
PostgreSQL
```

每一步的输入输出类型都是明确的，这是泛型 `BaseStage[TInput, TOutput]` 的价值。

---

## 💡 设计思考

**为什么 `embedding` 字段放在 `DocumentChunk` 里，而不是单独一个类？**

方案 A：`DocumentChunk` 不含向量，另有 `EmbeddedChunk(chunk_id, embedding)`

方案 B（当前）：`DocumentChunk.embedding` 可以为 None，由 Stage 填充

选方案 B 的原因：`EmbedStage` 的输入输出都是 `list[DocumentChunk]`，Pipeline 类型一致，不需要转换。代价是一个字段可能为 None，但用 `dataclasses.replace(chunk, embedding=vec)` 不可变更新，所以不会意外修改。

---

## 下一步

- 了解数据怎么在请求中流动 → [02 Context](./02-context.md)
- 了解 Stage 怎么处理这些数据 → [03 Stage](./03-stage.md)
