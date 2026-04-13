# 06 — ComponentRegistry

**对应代码**：`app/pipeline/core/registry.py`、`app/main.py`

## 🎯 读完本文你能回答

- Registry 是什么模式，解决了什么问题？
- 装饰器注册是怎么工作的？
- 为什么是单例？
- `main.py` 里 `_register_components()` 是必须的吗？

---

## Registry 解决的问题

**没有 Registry 时**，Strategy 需要直接 import 所有 Provider：

```python
from app.providers.embedding.openai_embedding import OpenAIEmbeddingProvider
from app.providers.embedding.ollama_embedding import OllamaEmbeddingProvider

class StandardStrategy:
    def build_pipeline(self, doc_type, config):
        if config.embedding_provider == "openai_embedding":
            provider = OpenAIEmbeddingProvider()
        elif config.embedding_provider == "ollama_embedding":
            provider = OllamaEmbeddingProvider()
        else:
            raise ValueError("unknown provider")
```

问题：每新增一个 Provider，就要修改 Strategy 里的 if-else。

**有 Registry 时**：

```python
class EmbedStage(BaseStage):
    async def _execute(self, ctx, input):
        provider = registry.get_provider(ctx.config.embedding_provider)
        # 不管 provider 是什么，统一按名字取
```

新增 Provider 只需注册，Strategy 和 Stage 代码完全不用改。

---

## 三张注册表

```python
class ComponentRegistry:
    _stages: dict[str, type[BaseStage]]      # "parser" → ParserStage 类
    _providers: dict[str, type[BaseProvider]] # "openai_embedding" → OpenAIEmbeddingProvider 类
    _strategies: dict[str, type[BaseStrategy]] # "standard" → StandardIngestionStrategy 类
```

注意存的是**类（type）**，不是实例。每次 `get_stage("parser")` 都会 `return self._stages[name]()`，即每次新建一个实例。这样 Stage 是无状态的，并发安全。

---

## 装饰器注册

```python
def stage(self, name: str):
    def decorator(cls: type) -> type:
        cls.name = name          # 顺手给类设置 name 属性
        self._stages[name] = cls # 把类存进注册表
        return cls               # 返回原类（装饰器不改变类本身）
    return decorator
```

使用时：

```python
# app/pipeline/stages/chunking/token_chunker.py

@registry.stage("token_chunker")
class TokenChunkerStage(BaseStage):
    ...
```

这等价于：

```python
class TokenChunkerStage(BaseStage):
    ...
TokenChunkerStage = registry.stage("token_chunker")(TokenChunkerStage)
# 即：registry._stages["token_chunker"] = TokenChunkerStage
```

**装饰器的执行时机**：模块被 `import` 的瞬间。所以只要 import 了模块，类就注册好了。

---

## 单例模式

```python
class ComponentRegistry:
    _instance: "ComponentRegistry | None" = None

    def __new__(cls) -> "ComponentRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._stages = {}
            cls._instance._providers = {}
            cls._instance._strategies = {}
        return cls._instance

registry = ComponentRegistry()  # 模块级别创建，整个进程共享这一个
```

无论在哪里 `from app.pipeline.core.registry import registry`，得到的都是同一个对象。

---

## 注册触发：main.py 的 lifespan

装饰器注册在 import 时执行，但 Python 不会自动 import 所有模块，需要主动触发：

```python
# app/main.py
def _register_components() -> None:
    import app.pipeline.stages.chunking.token_chunker   # 触发 @registry.stage("token_chunker")
    import app.pipeline.stages.embedding.embed_stage    # 触发 @registry.stage("embedder")
    import app.pipeline.stages.parsing.parser_stage     # 触发 @registry.stage("parser")
    import app.pipeline.strategies.ingestion.standard_strategy  # 触发 @registry.strategy("standard")
    import app.providers.embedding.openai_embedding     # 触发 @registry.provider("openai_embedding")
    import app.providers.parser.unstructured_provider   # 触发 @registry.provider("unstructured_parser")

@asynccontextmanager
async def lifespan(app: FastAPI):
    _register_components()   # 启动时注册所有组件
    yield
    await dispose()
```

如果忘记 import，运行时会抛 `StageNotFoundError: Stage 'parser' not registered`。

---

## 获取时的错误信息

```python
def get_stage(self, name: str) -> BaseStage:
    if name not in self._stages:
        raise StageNotFoundError(
            f"Stage '{name}' not registered. Available: {list(self._stages)}"
        )
    return self._stages[name]()
```

错误信息包含了所有已注册的名字，调试时直接知道哪些注册了：

```
StageNotFoundError: Stage 'paser' not registered.
Available: ['parser', 'token_chunker', 'embedder']
# 发现拼写错误：paser → parser
```

---

## 调试工具

```python
print(registry.list_stages())      # ['parser', 'token_chunker', 'embedder']
print(registry.list_providers())   # ['unstructured_parser', 'openai_embedding']
print(registry.list_strategies())  # ['standard']
```

---

## 💡 设计思考

**这和依赖注入框架（比如 FastAPI 的 Depends）有什么区别？**

| | ComponentRegistry | FastAPI Depends |
|---|---|---|
| 粒度 | 按字符串 ID 查找实现类 | 按函数签名类型注入 |
| 作用域 | 全局单例，应用生命周期 | 可以是请求级、会话级 |
| 场景 | 根据运行时配置选 Provider | 根据请求上下文注入服务 |

两者不冲突。`api/dependencies.py` 里用 FastAPI Depends 注入 tenant_id，
Pipeline 里用 Registry 选 Provider。

---

## 下一步

- 了解 Provider 是什么，有哪些实现 → [07 Provider](./07-provider.md)
- 了解 Strategy 如何使用 Registry → [08 Strategy](./08-strategy.md)
