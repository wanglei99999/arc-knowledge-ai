# ADR-041: 语义缓存 — 架构位置、索引策略与失效规则

**状态**: 已采纳  
**日期**: 2026-05-07  
**决策人**: 开发团队

---

## 背景

Phase 11b 目标：对重复或语义相近的问题跳过 RAG 检索 + LLM 调用，直接返回缓存答案，降低成本。需要决定：

1. 缓存逻辑放在哪一层（架构位置）
2. Redis 索引策略（全局索引 vs 每租户独立）
3. 缓存失效触发时机
4. EmbeddingProvider 与缓存的耦合方式

---

## 备选方案

### 缓存层位置

| 维度 | Provider 层（LangChain 模式） | Service 层（ChatService） | Workflow 层（RAGOrchestrator.chat()） |
|------|------------------------------|--------------------------|--------------------------------------|
| 能否跳过检索 | ❌ 检索始终执行 | ✅ 可跳过 | ✅ 可跳过 |
| 层级合规 | ✅ | ❌ services → infrastructure 跨层 | ✅ workflows → infrastructure |
| ChatService 是否感知缓存 | 否 | 是 | 否（封装在 Orchestrator） |
| 职责清晰度 | 中 | 低 | 高 |

### 索引策略

| 维度 | 全局单一索引（tenant_id TAG 过滤） | 每租户独立索引 |
|------|----------------------------------|---------------|
| 查询语法 | 需要 `@tenant_id:{xxx}` 过滤 | 直接 KNN 搜索 |
| 失效操作 | SCAN + DEL（复杂） | `FT.DROPINDEX`（一行） |
| 隔离性 | 逻辑隔离 | 物理隔离 |
| 扩展性 | 无限 | Redis Stack 索引数量有限，适合租户数有限的场景 |

---

## 决策

### 1. 缓存放在 `RAGOrchestrator.chat()`（Workflow 层）

RAGOrchestrator 是 RAG 全链路的协调器，"是否需要执行 RAG"属于其职责范围。将缓存逻辑封装在新增的 `chat()` 门面方法中，ChatService 对缓存完全无感知。

依赖方向：`workflows → infrastructure`，与现有 `RAGOrchestrator → ChunkRepository` 模式一致，不引入新的层级跨越。

### 2. 每租户独立 HNSW 索引

本项目租户数量有限，每租户独立索引带来：
- 天然数据隔离
- 失效时 `FT.DROPINDEX` 一行清除，无需 SCAN 遍历
- 查询无需 TAG 过滤，语法更简洁

### 3. 失效触发时机

| 操作 | 是否失效 | 原因 |
|------|---------|------|
| 文档删除 | ✅ | 旧 chunk_ids 物理删除，缓存引用断裂 |
| 文档重新上传 | ✅ | 旧 chunk_ids 全部替换为新 chunk_ids，同样断裂 |
| 新增文档 | ❌ | 已有缓存不受影响 |
| 仅更新元数据 | ❌ | chunk_ids 未变，缓存仍然有效 |

关键原因：缓存存储的是 `answer + citations`，citations 引用了具体的 chunk_ids。chunk_ids 失效即 citations 断裂，是数据完整性问题而非仅仅内容过时。

### 4. EmbeddingProvider 懒加载注入

`SemanticCache` 构造时接收可选的 `EmbeddingProvider`（测试注入），运行时通过 `registry.get_provider(name)` 懒加载，`initialize()` 时动态读 `get_dimension()`，不硬编码维度值。

### 5. 只缓存首轮问答（history=[]）

多轮对话中后续问题可能携带会话上下文（"我说的是海外出差"），语义缓存命中后返回首轮答案会导致错误。

---

## 后果

**正面**：
- 架构边界清晰，ChatService 不感知缓存细节
- 首轮重复问题命中率高，节省最多（检索 + LLM 全跳过）
- 每租户独立索引使失效操作简单可靠
- EmbeddingProvider 可替换，不绑定 OpenAI

**负面**：
- 多轮对话不使用缓存（设计决策，非技术限制）
- 每租户首次请求需懒创建索引，有轻微延迟（约 1 次 FT.CREATE RTT）
- 需要 Redis Stack（非标准 Redis），docker-compose 镜像需升级

---

## 参考

- `app/infrastructure/redis/semantic_cache.py`
- `app/workflows/rag_orchestrator.py`（`chat()` 方法）
- `docs/tech-notes/semantic-cache.md`：语义缓存全领域知识笔记
- ADR-040：限流中间件（同为 Phase 11 基础设施层）
