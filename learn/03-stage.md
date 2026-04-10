# 03 — BaseStage 与三个具体实现

**对应代码**：
- `app/pipeline/core/stage.py`
- `app/pipeline/stages/parsing/parser_stage.py`
- `app/pipeline/stages/chunking/token_chunker.py`
- `app/pipeline/stages/embedding/embed_stage.py`

## 🎯 读完本文你能回答

- `execute()` 和 `_execute()` 的区别是什么？
- `requires` 和 `produces` 是干什么的？
- 三个 Stage 分别做什么，输入输出是什么类型？

---

## BaseStage 设计

```python
class BaseStage(ABC, Generic[TInput, TOutput]):
    name: ClassVar[str]          # 注册名，如 "parser"
    requires: ClassVar[frozenset[str]] = frozenset()  # 需要 ctx.metadata 里有什么
    produces: ClassVar[frozenset[str]] = frozenset()  # 会往 ctx.metadata 写什么

    @abstractmethod
    async def _execute(self, ctx: ProcessingContext, input: TInput) -> TOutput:
        """子类实现业务逻辑的地方"""
        ...

    async def execute(self, ctx: ProcessingContext, input: TInput) -> TOutput:
        """框架调用入口，子类不要覆写"""
        self._check_preconditions(ctx)       # 自动检查前置条件
        return await self._execute(ctx, input)
```

**框架代码 vs 业务代码的分层**：

| 方法 | 谁来写 | 干什么 |
|------|-------|-------|
| `execute()` | 框架（BaseStage）| 前置检查、Hook 注入点 |
| `_execute()` | 业务代码（子类）| 实际处理逻辑 |

Pipeline 调用 `stage.execute()`，业务代码写 `_execute()`，两者互不干扰。

---

## Stage 1：ParserStage

```python
class ParserStage(BaseStage[RawFile, ParsedDocument]):
    name = "parser"
    produces = frozenset({"parsed_title"})  # 会往 ctx 写标题
```

**输入**：`RawFile`（文件路径 + mime 类型）

**输出**：`ParsedDocument`（结构化文本 + 标题）

**关键设计**：ParserStage 本身不知道怎么解析 PDF，它只是**委托**给 `ParserProvider`：

```python
async def _execute(self, ctx, input: RawFile) -> ParsedDocument:
    provider = self._get_provider(ctx)       # 从 registry 按名字取
    result = await provider.parse(ctx, input.file_path)

    # 顺手把标题写进 context，供后续 Stage 用
    if result.title:
        ctx.metadata["parsed_title"] = result.title

    return result
```

Provider 怎么来？

```python
def _get_provider(self, ctx) -> ParserProvider:
    if self._provider is not None:    # 测试时注入 mock
        return self._provider
    provider_id = getattr(ctx.config, "parser_provider", "unstructured_parser")
    return registry.get_provider(provider_id)  # 生产时从注册中心取
```

这里有一个测试友好设计：构造时传 `provider` 参数就用传入的（测试 mock），不传就从 registry 取（生产）。

---

## Stage 2：TokenChunkerStage

```python
class TokenChunkerStage(BaseStage[ParsedDocument, list[DocumentChunk]]):
    name = "token_chunker"
```

**输入**：`ParsedDocument`

**输出**：`list[DocumentChunk]`（每个 chunk 的 embedding=None）

**核心算法**（`_split_text` 函数）：

```
文本 → 按双换行拆成段落
       ↓
       每个段落累积 token 数
       ↓
       超过 chunk_size？→ 保存当前 chunk
                        → 取末尾若干段落作为 overlap（重叠）
                        → 继续累积
       ↓
       最后一批段落 → 最后一个 chunk
```

**为什么要 overlap（重叠）？**

文档切片时，如果相邻 chunk 完全没有重叠，问题的答案可能正好在两个 chunk 的边界处，检索时两个 chunk 都不完整。

```
Chunk 1: "...介绍了量子纠缠的基本"
Chunk 2: "原理，爱因斯坦将其称为..."
```

加了 overlap 后：

```
Chunk 1: "...介绍了量子纠缠的基本原理"  (包含 chunk 2 开头的几句)
Chunk 2: "量子纠缠的基本原理，爱因斯坦将其称为..."
```

每个 chunk 都包含上下文，检索结果更完整。

**token 估算**：

```python
def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)   # 1 token ≈ 4 个字符
```

Phase 0 用字符估算，不依赖外部库。Phase 1 可以换成 `tiktoken` 精确计算。

---

## Stage 3：EmbedStage

```python
class EmbedStage(BaseStage[list[DocumentChunk], list[DocumentChunk]]):
    name = "embedder"
    produces = frozenset({"embedding_model", "embedding_dimension"})
```

**输入**：`list[DocumentChunk]`（embedding=None）

**输出**：`list[DocumentChunk]`（embedding 已填充）

注意输入输出类型相同，只是填充了 `embedding` 字段。

**批处理**：

```python
BATCH_SIZE = 100   # OpenAI 单次最多 2048，保守取 100

for i in range(0, len(input), self._batch_size):
    batch = input[i : i + self._batch_size]
    texts = [chunk.content for chunk in batch]
    embeddings = await provider.embed(ctx, texts)

    for chunk, vec in zip(batch, embeddings):
        result.append(dataclasses.replace(chunk, embedding=vec))
        #              ↑ 不可变更新，返回新 chunk 对象
```

`dataclasses.replace(chunk, embedding=vec)` 创建新的 `DocumentChunk` 对象，原对象不变。

---

## 三个 Stage 的关系

```
RawFile ──[ParserStage]──→ ParsedDocument ──[TokenChunkerStage]──→ list[DocumentChunk]
                                                                          ↓
                                                                   [EmbedStage]
                                                                          ↓
                                                              list[DocumentChunk] (with embedding)
```

这是 Pipeline 的数据流：每个 Stage 的输出是下一个 Stage 的输入，类型在编译期就能检查。

---

## 💡 设计思考

**为什么 Stage 不直接调用 Provider，而是通过 registry 查找？**

直接调用：

```python
class ParserStage(BaseStage):
    def __init__(self):
        self._provider = UnstructuredParserProvider()  # 硬编码
```

通过 registry：

```python
class ParserStage(BaseStage):
    def _get_provider(self, ctx):
        return registry.get_provider("unstructured_parser")  # 按名字查找
```

区别：
1. **可替换**：换 Provider 只需注册新实现，改租户配置，不改 Stage 代码
2. **可测试**：测试时注入 `FakeProvider`，不需要真实的 Unstructured 库
3. **懒加载**：Provider 只在需要时实例化，启动更快

---

## 下一步

- 了解 Stage 怎么被串联起来 → [04 Pipeline](./04-pipeline.md)
- 了解 Provider 的具体实现 → [07 Provider](./07-provider.md)
