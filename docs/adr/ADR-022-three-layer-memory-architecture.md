# ADR-022: 采用三层记忆架构实现对话记忆系统

**状态**: 已采纳
**日期**: 2026-04-13
**决策人**: 项目团队

---

## 背景

Phase 2 的 RAG 问答依赖调用方每次传入完整 `history`，存在以下问题：
- 客户端负担重，需要维护所有历史消息
- 对话越长 token 消耗越大，最终超出 context window
- 无法在会话之间保留用户知识（今天聊过的事明天不记得）
- 无从了解用户偏好，无法提供个性化回答

需要在服务端实现完整的对话记忆能力。

---

## 备选方案

| 方案 | 描述 | 问题 |
|------|------|------|
| A：全量历史存 DB，每次全量加载 | 最简单 | context window 溢出；token 成本随对话线性增长 |
| B：滑动窗口（最近 N 条） | 简单易实现 | 丢失超出窗口的历史；无跨会话记忆 |
| C：LLM 摘要压缩 | 压缩旧历史 | 只解决单会话问题；摘要有信息损失 |
| **D：三层记忆（采纳）** | 工作记忆 + 情节记忆 + 语义记忆 | 实现复杂，但能力完整 |

---

## 决策

选择**方案 D：三层记忆架构**，参考 mem0 / Zep / MemGPT 的工业实践：

```
Layer 1 — 工作记忆（Working Memory）
  最近 10 条消息，直接放入 context window
  → 解决：当前会话连贯性

Layer 2 — 情节记忆（Episodic Memory）
  消息数 > 20 时 LLM 压缩旧消息为摘要，存 sessions.summary
  → 解决：长对话 context 溢出

Layer 3 — 语义记忆（Semantic Memory）
  LLM 提取对话中的事实/偏好，向量化存 Milvus + PG
  查询时语义搜索相关记忆注入 context
  → 解决：跨会话知识保留、用户个性化
```

---

## ContextAssembler Token 预算策略

Context window（8192 tokens）按优先级分配：

| 区域 | 上限 | 优先级 |
|------|------|-------|
| System prompt | ~200 | 最高（固定） |
| RAG chunks（知识库内容） | ≤3000 | 高 |
| Recent messages（最近 10 条） | ≤2000 | 高 |
| Session summary（历史摘要） | ≤500 | 中 |
| Long-term memories（top-5） | ≤1000 | 低 |
| Response buffer | ~500 | 保留 |

预算不足时从低优先级（memories → summary）依次截断，保证 RAG 内容和近期对话优先。

---

## 后果

**正面**：
- 对话连贯性有保证（工作记忆）
- 长对话不溢出（情节记忆压缩）
- 跨会话记忆用户偏好（语义记忆）
- token 成本可控（提取的事实 vs. 全量历史，节省约 80%）

**负面**：
- 实现复杂，新增约 10 个文件
- 记忆提取有 LLM 调用成本（每次对话后后台异步调用）
- 提取的记忆存在错误/遗漏（LLM 不完美），需要后续置信度优化

---

## 参考

- mem0 架构：LLM-guided ADD/UPDATE/DELETE memory management
- Zep：Temporal knowledge graph + hybrid retrieval
- MemGPT：OS-inspired hierarchical memory tiers
- `app/memory/` — 实现目录
- ADR-023 — 记忆提取为何选择异步后台任务
