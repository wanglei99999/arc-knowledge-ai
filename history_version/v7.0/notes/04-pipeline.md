# 04 — Pipeline

**对应代码**：`app/pipeline/core/pipeline.py`

## 🎯 读完本文你能回答

- Pipeline 的 `run()` 方法内部发生了什么？
- `then()` 为什么返回新 Pipeline，而不是修改自身？
- `as_stage()` 是什么黑魔法？

---

## Pipeline 的职责

Pipeline 只做两件事：

1. **按顺序调用 Stage**
2. **在每个 Stage 前后触发 Hook**

它本身**不包含任何业务逻辑**。

---

## run() 方法逐行解析

```python
async def run(self, ctx: ProcessingContext, input: TInput) -> TOutput:
    # 1. 整条 Pipeline 开始前触发 Hook（Phase 3 的 TenantGuard 在这里运行）
    await self._hook_runner.fire(Phase.PRE_PIPELINE, ctx)

    current = input
    for stage in self.stages:
        # 2. 每个 Stage 执行前触发 Hook
        result = await self._hook_runner.fire(Phase.PRE_STAGE, ctx, stage=stage)

        # 3. 如果 Hook 返回 SKIP_STAGE（幂等：这个 Stage 已执行过），跳过
        if result == HookResult.SKIP_STAGE:
            continue

        try:
            # 4. 执行 Stage（自动做前置条件检查）
            current = await stage.execute(ctx, current)

            # 5. Stage 执行后触发 Hook（ObservabilityHook 在这里记录耗时）
            await self._hook_runner.fire(Phase.POST_STAGE, ctx, stage=stage)

        except Exception as e:
            # 6. 出错时触发 Hook（告警 Hook 在这里触发）
            await self._hook_runner.fire(Phase.ON_ERROR, ctx, stage=stage, error=e)
            raise

    # 7. 整条 Pipeline 结束后触发 Hook（QuotaGuard 在这里扣减消耗）
    await self._hook_runner.fire(Phase.POST_PIPELINE, ctx)
    return current
```

Phase 0 的 `StandardIngestionStrategy.hooks = []`，所以 `fire()` 调用立即返回 `CONTINUE`，等于无 Hook 状态。

---

## 链式构造 API

```python
pipeline = (
    Pipeline.start(ParserStage())       # 创建只有一个 Stage 的 Pipeline
            .then(TokenChunkerStage())  # 追加第二个 Stage，返回新 Pipeline
            .then(EmbedStage())         # 再追加第三个，返回新 Pipeline
)
```

`then()` 的实现：

```python
def then(self, stage: BaseStage) -> "Pipeline":
    return Pipeline(
        stages=[*self.stages, stage],   # 展开原有 stages，追加新 stage
        hooks=self._hook_runner.hooks   # 保留原有 hooks
    )
```

**为什么返回新 Pipeline，不修改 self？**

```python
p1 = Pipeline.start(ParserStage())
p2 = p1.then(TokenChunkerStage())   # p1 还是只有一个 Stage
p3 = p1.then(EmbedStage())          # 可以从 p1 出发构造不同的 Pipeline
```

不可变构造允许"从同一个基础 Pipeline 衍生出多个变体"，在多租户场景（不同租户有不同 Pipeline 配置）很有用。

---

## as_stage()：Pipeline 嵌套

```python
# 把检索 Pipeline 包装成一个 Stage
retrieval_sub = retrieval_pipeline.as_stage("retrieval")

# 在 RAG Pipeline 里直接复用
rag_pipeline = (
    Pipeline.start(QueryRewriteStage())
            .then(retrieval_sub)         # ← 一整条检索 Pipeline 作为一个 Stage
            .then(ContextPackStage())
            .then(LLMStreamStage())
)
```

`_PipelineStage` 的实现：

```python
class _PipelineStage(BaseStage):
    async def _execute(self, ctx, input):
        return await self._pipeline.run(ctx, input)
        # 就是调用内部 Pipeline 的 run()
```

这是**组合优于继承**的体现。RAG Pipeline 不需要继承检索 Pipeline，只需要把它"嵌入"进来。

---

## with_hooks()：给 Pipeline 附加 Hook

```python
# Strategy 构建带 Hook 的 Pipeline
def build_pipeline_with_hooks(self, doc_type, config) -> Pipeline:
    return self.build_pipeline(doc_type, config).with_hooks(self.get_hooks())
```

`with_hooks()` 也返回新 Pipeline：

```python
def with_hooks(self, hooks: list[BaseHook]) -> "Pipeline":
    return Pipeline(stages=self.stages, hooks=hooks)
```

---

## 完整的 repr

```python
def __repr__(self) -> str:
    names = " → ".join(s.name for s in self.stages)
    return f"Pipeline([{names}])"
```

打印出来是：

```
Pipeline([parser → token_chunker → embedder])
```

调试时一目了然。

---

## 💡 设计思考

**为什么不直接写一个函数而要有 Pipeline 这个类？**

```python
# 没有 Pipeline 类，直接写函数
async def run_ingestion(ctx, file):
    parsed = await parse(ctx, file)
    chunks = await chunk(ctx, parsed)
    embedded = await embed(ctx, chunks)
    return embedded
```

这样写的问题：
- 没有 Hook 注入点（无法透明地加监控、配额检查）
- 无法动态组合（不同租户用不同函数？要写很多 if-else）
- 无法嵌套复用（RAG 里想复用检索逻辑，只能复制代码）

Pipeline 把"编排"和"执行"分开：Strategy 决定**怎么组合**，Pipeline 负责**按顺序跑**，Stage 只关心**自己那一步**。

---

## 下一步

- 了解 Hook 系统如何注入横切能力 → [05 Hook](./05-hook.md)
- 了解 Strategy 如何决定 Pipeline 的组合 → [08 Strategy](./08-strategy.md)
