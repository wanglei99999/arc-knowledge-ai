# ArcKnowledge AI — 学习文档

按顺序阅读，每篇文档对应一个核心概念或模块。
建议先读完 00~02（架构全貌），再按需深入某个模块。

---

## 阅读顺序

### Phase 0（v1.0）— Pipeline 框架

| 编号 | 文档 | 核心问题 | 对应代码 |
|------|------|---------|---------|
| [00](./00-overview.md) | 架构全貌 | 整个系统是怎么组织的？ | — |
| [01](./01-domain-model.md) | 领域模型 | 数据长什么样？ | `app/domain/document.py` |
| [02](./02-context.md) | ProcessingContext | 请求信息怎么在系统里流动？ | `pipeline/core/context.py` |
| [03](./03-stage.md) | BaseStage | 最小处理单元怎么设计？ | `pipeline/core/stage.py` |
| [04](./04-pipeline.md) | Pipeline | Stage 怎么串联？ | `pipeline/core/pipeline.py` |
| [05](./05-hook.md) | Hook 系统 | 横切能力（配额/日志/幂等）怎么注入？ | `pipeline/core/hook.py` |
| [06](./06-registry.md) | Registry | 组件怎么注册和查找？ | `pipeline/core/registry.py` |
| [07](./07-provider.md) | Provider 抽象 | AI 能力（解析/向量化/LLM）怎么封装？ | `providers/base.py` + 实现 |
| [08](./08-strategy.md) | Strategy 模式 | Pipeline 怎么动态组合？ | `pipeline/strategies/` |
| [09](./09-workflow.md) | Temporal 工作流 | 长流程怎么保证不丢失？ | `workflows/` |
| [10](./10-service-api.md) | Service + API 层 | 请求从 HTTP 到业务逻辑的路径 | `services/` + `api/` |
| [11](./11-full-flow.md) | 完整链路追踪 v1 | 一个文件上传后发生了什么？（Phase 0） | 全部 |
| [12](./python_basic_knowledge/01-python-basics.md) | Python 语法补充 | 看懂项目里常见的抽象类、泛型与 `with` 语法 | `stage.py` + `workflow.py` |

### Phase 1（v2.0）— 完整 Ingestion

| 编号 | 文档 | 核心问题 | 对应代码 |
|------|------|---------|---------|
| [13](./13-infrastructure.md) | 基础设施层 | MinIO / Milvus / PG 各存什么？run_in_executor 怎么用？ | `infrastructure/` |
| [14](./14-full-flow-v2.md) | 完整链路追踪 v2 | Phase 1 加入 MinIO 上传和 Milvus 写入后链路怎么变？ | 全部 |

### Phase 2（v3.0）— RAG 检索生成

| 编号 | 文档 | 核心问题 | 对应代码 |
|------|------|---------|---------|
| [15](./15-retrieval-pipeline.md) | Retrieval Pipeline | SearchContext 是什么？RRF 怎么算？为什么顺序 Pipeline 能做混合检索？ | `pipeline/stages/retrieval/` |
| [16](./16-full-flow-v3.md) | 完整链路追踪 v3 | 一次搜索和一次问答各经历了什么？SSE 怎么工作？ | 全部 |

---

## 快速参考

### 核心抽象关系图

```
ProcessingContext  ←  贯穿所有组件，携带 tenant/trace/quota/metadata
       │
       ▼
   Pipeline ──────── HookRunner（横切：配额/幂等/观测）
       │
   [Stage₁] → [Stage₂] → [Stage₃]
       │
   Provider（实际调 OpenAI/Unstructured/PaddleOCR）
       │
   Infrastructure（PG/Milvus/MinIO）
```

### 注册 → 使用 流程

```
模块导入时：@registry.stage("parser") 自动注册
main.py lifespan：import 所有模块触发注册
运行时：registry.get_stage("parser") → ParserStage()
```

### 依赖方向（只能单向）

```
domain ← infrastructure ← providers
                      ↑
         pipeline/core ← pipeline/stages ← pipeline/strategies
                                      ↑
                                  workflows ← services ← api
```
