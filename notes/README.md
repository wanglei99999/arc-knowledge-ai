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
| [12-2](./python_basic_knowledge/02-decorators.md) | Python 装饰器 | 为什么 `@xxx(...)` 总会变成 `def -> def -> return`？ | `registry.py` |

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

### Phase 3（v4.0）— 多租户 + 横切关注点

| 编号 | 文档 | 核心问题 | 对应代码 |
|------|------|---------|---------|
| [17](./17-hooks-jwt.md) | Hook 实现 + JWT 认证 | 四个 Hook 各做什么？IdempotencyGuard 如何防重复？JWT Depends 怎么工作？ | `pipeline/hooks/` + `api/dependencies.py` |
| [18](./18-full-flow-v4.md) | 全链路追踪 v4 | JWT 认证如何接入？Hook 如何在 Pipeline 每个 Stage 前后触发？ | 全部 |

### Phase 4（v5.0）— 高可用 + 可观测性

> 请求链路与 v4 相同，无需新增 full-flow 文档。
> ObservabilityHook 在 POST_STAGE 额外发送 OTel Span 和 Prometheus metrics，属于副作用，不改变主链路。
> 架构决策见 [ADR-016](../docs/adr/ADR-016-opentelemetry-observability.md) 和 [ADR-017](../docs/adr/ADR-017-temporal-workflow-versioning.md)。

### Phase 5（v6.0）— 多模型路由

> 请求链路与 v4 相同，无需新增 full-flow 文档。
> LLM 调用路径新增 ModelHub → ResilientLLMProvider 层，对 RAGOrchestrator 透明。
> 架构决策见 [ADR-018](../docs/adr/ADR-018-model-hub-resilient-routing.md) 和 [ADR-019](../docs/adr/ADR-019-ollama-hot-switch.md)。
>
> ⚠️ **已知局限**：`model_state.py` 全局可变状态在 K8s 多副本时不一致；`TenantConfig` 仍为硬编码默认值。Phase 10（v11.0）修复。

### Phase 6（v7.0）— 文档删除

> 📋 **设计中。** 联动清理 MinIO / Milvus / ES / PG，补全文档生命周期。

### Phase 7（v8.0）— 会话持久化

> 📋 **设计中。** 新增 `sessions` + `messages` 表，服务端存储对话历史，支持真正的多轮对话管理。

### Phase 8（v9.0）— 检索质量提升

> 📋 **设计中。** QueryRewriteStage 接入 LLM 做多 Query 扩展；RerankStage 接入 BGE-Reranker，替换两个长期 pass-through。

### Phase 9（v10.0）— 测试补全

> 📋 **设计中。** 补 Phase 1–5 的零测试状态，覆盖 Hook / ResilientLLMProvider / RAGOrchestrator / 删除 / 会话等核心路径。

### Phase 10（v11.0）— 多租户模型路由重构

> 📋 **设计中。** 对应原 Phase 6 多租户模型路由设计内容：删除 `model_state.py` 全局状态，新增 `tenant_configs` 表，三层模型解析。

### Phase 11（v12.0）— 限流 + 语义缓存

> 📋 **设计中。** Redis 滑动窗口限流 + 语义相似度缓存，防滥用 + 降 LLM 调用成本。

### Phase 12（v13.0）— 用量追踪

> 📋 **设计中。** 对应原 Phase 7 用量追踪设计内容：`model_configs` 定价表 + `usage_records` 流水，token 消耗可见。

### Phase 13（v14.0）— 可观测性完善 + Admin UI

> 📋 **设计中。** Grafana 看板 + Prometheus 告警规则 + 最小 Admin UI，系统达到可运营状态。

### Phase 14+（v15.0+）— Java 控制面

> 📋 **待规划，Python MVP 稳定后启动。** arc-gateway / arc-auth / arc-tenant / arc-knowledge / arc-user / arc-audit，Python 临时鉴权逻辑正式移交。

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
