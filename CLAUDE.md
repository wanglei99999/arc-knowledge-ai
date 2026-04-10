# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# 安装依赖
pip install -e ".[dev]"

# 启动 FastAPI（开发模式）
uvicorn app.main:app --reload

# 启动 Temporal Worker
python scripts/start_worker.py

# 运行全部测试
pytest

# 运行单个测试文件
pytest tests/unit/stages/test_token_chunker.py

# 运行单个测试用例
pytest tests/unit/pipeline/test_pipeline.py::test_pipeline_runs_stages_in_order

# 代码格式化
black app tests
ruff check app tests --fix

# 类型检查
mypy app
```

## 架构

六层结构，依赖方向严格单向（上层调下层，禁止反向）：

```
api → services → workflows → pipeline/stages → providers → infrastructure
```

### Pipeline 框架（`app/pipeline/core/`）

这是整个系统的核心，理解这四个文件是开展其他工作的前提：

- **`context.py`**：`ProcessingContext` 是不可变数据类，贯穿所有 Stage。`with_metadata()` 返回新实例而不是修改自身。每个 Temporal Activity 通过 `_make_context(inp)` 独立重建，不跨 Activity 传递。
- **`stage.py`**：`BaseStage[TInput, TOutput]` 泛型 ABC。`execute()` 是框架层（检查 `requires`/`produces` 前后置条件），`_execute()` 是业务层（子类实现）。
- **`pipeline.py`**：`then()` 返回新 Pipeline 实例（不可变 builder）。`as_stage()` 将整条 Pipeline 包装成一个 Stage，用于 Pipeline 嵌套。
- **`registry.py`**：全局单例，三张表（stage / provider / strategy）。注册在模块 import 时通过装饰器触发。**每次 `get_stage()` / `get_provider()` 都返回新实例**，Stage 和 Provider 设计为无状态。

### 注册机制（关键约束）

新增任何 Stage / Provider / Strategy 后，必须在 `app/main.py` 的 `_register_components()` 里添加对应的 import，否则运行时抛 `StageNotFoundError`。装饰器注册只在模块被 import 时执行，Python 不会自动 import 未被引用的模块。

### Provider 与 Stage 的边界

- Stage 知道 Pipeline 上下文（读 `ctx.config`，决定批量大小等）——业务逻辑
- Provider 只知道调用参数，不知道 Pipeline——技术实现

Stage 构造函数接受可选的 `provider` 参数用于测试注入；不传则从 registry 按 `ctx.config.xxx_provider` 取。

### Temporal Activity 约束

- Workflow 代码必须确定性（不能有 IO、随机数、`datetime.now()`）
- Activity 的输入/输出必须 JSON 可序列化（用 dataclass，不传 ProcessingContext）
- `IngestionInput` 包含租户配置快照，保证同一文档的三个 Activity 使用一致配置

### Hook 系统（当前未激活）

`StandardIngestionStrategy.hooks = []`，Phase 3 会填入 `[TenantGuard, QuotaGuard, IdempotencyGuard, ObservabilityHook]`。Hook 通过 `priority` 整数控制执行顺序（数值越小越先执行）。现阶段不需要修改 Hook 相关代码。

## 测试

单元测试使用 `conftest.py` 中的 `fake_ctx` fixture，不启动任何外部依赖。需要替换真实 Provider 时，继承对应的抽象类实现 Fake 版本：

```python
class FakeEmbeddingProvider(EmbeddingProvider):
    async def embed(self, ctx, texts): return [[0.0] * 8 for _ in texts]
    def get_dimension(self): return 8
    def get_model_name(self): return "fake"

stage = EmbedStage(provider=FakeEmbeddingProvider())
```

`pytest-asyncio` 已配置 `asyncio_mode = "auto"`，异步测试函数直接用 `async def` 即可，不需要 `@pytest.mark.asyncio`。

## 扩展点

| 场景 | 做法 |
|------|------|
| 新增 Provider | 新建文件，加 `@registry.provider("id")`，在 `main.py` import |
| 新增 Stage | 新建文件，加 `@registry.stage("id")`，在 `main.py` import |
| 新增处理方案 | 新建 Strategy 类，加 `@registry.strategy("id")`，在 `main.py` import |
| 切换租户 Provider | 修改租户配置中的 `embedding_provider` / `parser_provider` 字段 |
