# 08 — Strategy 模式

**对应代码**：
- `app/pipeline/strategies/base_strategy.py`
- `app/pipeline/strategies/ingestion/standard_strategy.py`

## 🎯 读完本文你能回答

- Strategy 和 Pipeline 的关系是什么？
- 怎么实现"不同租户用不同处理方案"？
- `hooks` 类变量是干什么的？

---

## Strategy 解决的问题

不同场景需要不同的 Pipeline 组合：

| 场景 | Pipeline 组合 |
|------|-------------|
| 普通文档 | Parser → Chunker → Embedder |
| 扫描件 PDF | OCR → Parser → Chunker → Embedder |
| 付费用户 | OCR → Parser → Chunker → Summarizer → Embedder |
| 纯文本 | TextParser → Chunker → Embedder（跳过 OCR） |

如果在代码里写 if-else：

```python
def build_pipeline(doc_type, is_premium, has_ocr):
    stages = []
    if has_ocr:
        stages.append(OCRStage())
    stages.append(ParserStage())
    stages.append(ChunkerStage())
    if is_premium:
        stages.append(SummarizerStage())
    stages.append(EmbedStage())
    return Pipeline(stages)
```

条件越来越多，越来越难维护。

**Strategy 的做法**：每种方案是一个独立的类，不同场景注册不同的 Strategy：

```python
@registry.strategy("standard")
class StandardIngestionStrategy(BaseStrategy):
    def build_pipeline(self, doc_type, config):
        return Pipeline.start(ParserStage()).then(TokenChunkerStage()).then(EmbedStage())

@registry.strategy("ocr_ingestion")
class OCRIngestionStrategy(BaseStrategy):
    def build_pipeline(self, doc_type, config):
        return (
            Pipeline.start(OCRStage())         # 多了这一步
            .then(ParserStage())
            .then(TokenChunkerStage())
            .then(EmbedStage())
        )

@registry.strategy("premium_ingestion")
class PremiumIngestionStrategy(BaseStrategy):
    def build_pipeline(self, doc_type, config):
        return (
            Pipeline.start(OCRStage())
            .then(ParserStage())
            .then(TokenChunkerStage())
            .then(SummarizerStage())            # 付费用户多了摘要
            .then(EmbedStage())
        )
```

租户配置里设 `ingestion_strategy = "ocr_ingestion"`，就自动走 OCR 流程。

---

## BaseStrategy

```python
class BaseStrategy(ABC):
    strategy_id: ClassVar[str]
    hooks: ClassVar[list[type[BaseHook]]] = []  # 声明需要哪些 Hook

    @abstractmethod
    def build_pipeline(self, doc_type: str, config: TenantConfig) -> Pipeline: ...

    def get_hooks(self) -> list[BaseHook]:
        return [hook_cls() for hook_cls in self.hooks]  # 实例化所有 Hook

    def build_pipeline_with_hooks(self, doc_type, config) -> Pipeline:
        return self.build_pipeline(doc_type, config).with_hooks(self.get_hooks())
```

---

## StandardIngestionStrategy（当前实现）

```python
@registry.strategy("standard")
class StandardIngestionStrategy(BaseStrategy):
    strategy_id = "standard"
    hooks = []   # Phase 0 无 Hook

    def build_pipeline(self, doc_type: str, config: TenantConfig) -> Pipeline:
        return (
            Pipeline.start(ParserStage())
            .then(TokenChunkerStage())
            .then(EmbedStage())
        )
```

Phase 3 激活 Hook 时，只需要改 `hooks` 这一行：

```python
from app.pipeline.hooks.tenant_guard import TenantGuard
from app.pipeline.hooks.quota_guard import QuotaGuard
from app.pipeline.hooks.idempotency_guard import IdempotencyGuard
from app.pipeline.hooks.observability_hook import ObservabilityHook

hooks = [TenantGuard, QuotaGuard, IdempotencyGuard, ObservabilityHook]
# build_pipeline() 完全不用改
```

---

## Strategy 的 hooks 字段

不同 Strategy 可以有不同的 Hook 组合：

```python
@registry.strategy("standard")
class StandardIngestionStrategy(BaseStrategy):
    hooks = [TenantGuard, ObservabilityHook, QuotaGuard]

@registry.strategy("premium_ingestion")
class PremiumIngestionStrategy(BaseStrategy):
    hooks = [
        TenantGuard,
        ObservabilityHook,
        QuotaGuard,
        ContentSafetyCheck,    # 付费用户额外检查内容安全
        ComplianceAuditHook,   # 金融/政府客户需要合规审计
    ]
```

付费租户配置 `ingestion_strategy = "premium_ingestion"`，自动获得额外的 Hook，无需改代码。

这是 **Hook Registry** 的核心价值：**不同 Strategy 声明不同的横切能力**。

---

## 运行时选择 Strategy

在 Temporal Activity 里：

```python
# ingestion_activities.py

async def parse_activity(inp: IngestionInput) -> dict:
    ctx = _make_context(inp)
    # ctx.config.ingestion_strategy = "standard"（从租户配置来）

    # Strategy 决定 Pipeline 怎么组合
    strategy = registry.get_strategy(ctx.config.ingestion_strategy)
    pipeline = strategy.build_pipeline(inp.mime_type, ctx.config)
    # → Pipeline([parser → token_chunker → embedder])

    result = await pipeline.run(ctx, raw_file)
```

实际上 Phase 0 的 Activity 还没用 Strategy（直接调了 `registry.get_stage()`），Phase 1 重构时会统一走 Strategy。

---

## 💡 设计思考

**Strategy 模式 vs 工厂模式有什么区别？**

| | 工厂模式 | Strategy 模式 |
|---|---------|-------------|
| 目的 | 创建对象 | 定义算法族，使其可替换 |
| 切换时机 | 创建时决定 | 运行时决定 |
| 在这里的应用 | Registry 是工厂 | Strategy 是算法选择 |

在这套架构里两者都有：
- **Registry** 是工厂（按名字创建 Stage/Provider 实例）
- **Strategy** 是算法选择（决定用什么 Stage 组合）

Strategy 内部调用 Registry，两者协同工作。

---

## 下一步

- 了解 Temporal 如何把所有东西串起来 → [09 Workflow](./09-workflow.md)
- 了解 Service 和 API 层 → [10 Service+API](./10-service-api.md)
