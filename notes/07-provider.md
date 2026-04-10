# 07 — Provider 抽象与实现

**对应代码**：
- `app/providers/base.py`
- `app/providers/parser/unstructured_provider.py`
- `app/providers/embedding/openai_embedding.py`

## 🎯 读完本文你能回答

- Provider 抽象和 Stage 有什么区别？
- 为什么 `UnstructuredParserProvider` 用 `run_in_executor`？
- 怎么给 Provider 写单元测试？

---

## Provider 是什么

Provider 是**外部 AI 能力的可替换实现**。

Stage 描述"做什么"（解析、切片、向量化），Provider 描述"怎么做"（用哪个库、调哪个 API）。

| Stage | Provider | 关系 |
|-------|---------|------|
| `ParserStage` | `UnstructuredParserProvider` | Stage 委托 Provider 解析 |
| `EmbedStage` | `OpenAIEmbeddingProvider` | Stage 委托 Provider 向量化 |
| 未来的 `LLMStreamStage` | `OpenAILLMProvider` / `OllamaLLMProvider` | 可替换 |

---

## 四个 Provider 接口

```python
# providers/base.py

class EmbeddingProvider(BaseProvider):
    async def embed(self, ctx, texts: list[str]) -> list[list[float]]: ...
    def get_dimension(self) -> int: ...     # 向量维度（Milvus 建 Collection 需要）
    def get_model_name(self) -> str: ...

class LLMProvider(BaseProvider):
    async def generate(self, ctx, messages) -> str: ...           # 非流式
    async def stream_generate(self, ctx, messages) -> AsyncIterator[str]: ...  # 流式

class ParserProvider(BaseProvider):
    async def parse(self, ctx, file_path: str) -> ParsedDocument: ...
    def supports(self, mime_type: str) -> bool: ...   # 声明支持哪些文件类型

class RerankProvider(BaseProvider):
    async def rerank(self, ctx, query, documents, top_n) -> list[tuple[int, float]]: ...
    # 返回 [(原始索引, 相关性分数)]，按分数降序
```

每种 Provider 有独立的抽象接口，不同接口互不混用。

---

## UnstructuredParserProvider 解析

```python
@registry.provider("unstructured_parser")
class UnstructuredParserProvider(ParserProvider):

    async def parse(self, ctx, file_path: str) -> ParsedDocument:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._parse_sync, file_path)
        # ↑ 把同步阻塞操作放到线程池执行，不阻塞事件循环

    def _parse_sync(self, file_path: str) -> ParsedDocument:
        from unstructured.partition.auto import partition  # 懒加载

        elements = partition(filename=file_path)
        # elements 是结构化元素列表：[Title("标题"), Text("正文"), Table("表格数据"), ...]

        title = next(
            (str(el) for el in elements if el.category == "Title"),
            None
        )

        parts = []
        for el in elements:
            text = str(el).strip()
            if not text:
                continue
            if el.category == "Title":
                parts.append(f"\n\n{text}")    # 标题前加空行，分割段落
            elif el.category == "Table":
                parts.append(f"\n{text}\n")
            else:
                parts.append(text)

        return ParsedDocument(text="\n".join(parts).strip(), title=title)
```

**为什么用 `run_in_executor`？**

`partition()` 是同步函数，会阻塞当前线程几秒钟（解析一个 PDF）。

FastAPI 和 Temporal 都是异步的（基于 asyncio 事件循环）。如果在事件循环里执行同步阻塞代码，整个服务在这段时间内无法处理其他请求。

`run_in_executor` 把同步函数扔到线程池（默认 CPU 核数个线程），事件循环继续处理其他请求，等线程池里的任务完成后，`await` 拿到结果。

**懒加载的原因**：

```python
def _parse_sync(self, file_path: str) -> ParsedDocument:
    from unstructured.partition.auto import partition   # 每次调用时才导入
```

Unstructured 库启动时加载很慢（几秒），如果在模块顶部导入，每次启动服务都要等这几秒。懒加载使得第一次解析慢，但服务启动快。

---

## OpenAIEmbeddingProvider 解析

```python
@registry.provider("openai_embedding")
class OpenAIEmbeddingProvider(EmbeddingProvider):

    def __init__(self, api_key=None, model="text-embedding-3-small"):
        import openai
        self._client = openai.AsyncOpenAI(api_key=api_key or os.environ["OPENAI_API_KEY"])
        self._model = model

    async def embed(self, ctx, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        response = await self._client.embeddings.create(
            model=self._model,
            input=texts,
        )
        # API 返回的顺序不保证与输入一致，必须按 index 排序
        sorted_data = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in sorted_data]
```

**为什么要排序？**

OpenAI 的 embeddings API 返回的 `data` 数组不保证顺序。如果不排序，第 3 个文本可能对应第 1 个向量，写进数据库就乱了。

`item.index` 是原始输入的位置索引，按它排序保证输出和输入一一对应。

---

## 怎么写 Provider 的单元测试

不能每次跑测试都真的调 OpenAI，做法是写一个 Fake：

```python
# tests/unit/stages/test_embed_stage.py

class FakeEmbeddingProvider(EmbeddingProvider):
    DIM = 8

    async def embed(self, ctx, texts: list[str]) -> list[list[float]]:
        return [[float(i)] * self.DIM for i in range(len(texts))]
        # 返回固定的假向量，不走网络

    def get_dimension(self) -> int:
        return self.DIM

    def get_model_name(self) -> str:
        return "fake-model"
```

使用时注入：

```python
stage = EmbedStage(provider=FakeEmbeddingProvider())
# Stage 构造函数支持传入 provider 参数，就是为了测试
```

这就是 Stage 设计里"构造时传 provider 就用传入的，否则从 registry 取"的价值。

---

## 扩展：添加新 Provider

比如要添加 Ollama 本地嵌入：

```python
# app/providers/embedding/ollama_embedding.py

@registry.provider("ollama_embedding")
class OllamaEmbeddingProvider(EmbeddingProvider):

    def __init__(self, base_url="http://localhost:11434", model="nomic-embed-text"):
        self._base_url = base_url
        self._model = model

    async def embed(self, ctx, texts):
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": texts}
            )
            return response.json()["embeddings"]

    def get_dimension(self): return 768
    def get_model_name(self): return self._model
```

然后在 `main.py` 的 `_register_components()` 里加一行 import。

不需要改 `EmbedStage`，不需要改 `StandardIngestionStrategy`。只要租户配置里设 `embedding_provider = "ollama_embedding"`，就会自动使用。

---

## 💡 设计思考

**Provider 和 Stage 的职责边界**

| | Stage | Provider |
|---|---|---|
| 知道 Pipeline | 是（知道上下游） | 否 |
| 知道 Context | 是（接受 ctx 参数） | 是（接受 ctx 参数） |
| 包含业务逻辑 | 是（批处理、参数读取） | 否（只管调 API） |
| 可替换 | 否（一种能力一个 Stage） | 是（同一种能力多种实现） |

例子：`EmbedStage` 知道"要批量处理"、"要从 ctx.config 读 provider_id"——这是业务逻辑。`OpenAIEmbeddingProvider` 只知道"调 OpenAI API，返回向量"——这是技术实现。

---

## 下一步

- 了解 Strategy 如何把 Stage 和 Provider 组装起来 → [08 Strategy](./08-strategy.md)
- 了解 Temporal 如何调用 Pipeline → [09 Workflow](./09-workflow.md)
