# ADR-023: 记忆提取采用异步后台任务，不阻塞 SSE 响应

**状态**: 已采纳
**日期**: 2026-04-13
**决策人**: 项目团队

---

## 背景

Phase 7 需要在每次对话结束后，从本轮对话中提取结构化事实存入长期记忆。提取过程需要调用 LLM（一次 inference 请求），耗时约 500ms–2s。

同时，当消息数超过阈值时，还需要调用 LLM 压缩历史为摘要，同样耗时约 1–3s。

两个操作叠加在 SSE 响应路径上，会显著拉长用户感知延迟。

---

## 备选方案

| 方案 | 描述 | 延迟影响 |
|------|------|---------|
| A：同步阻塞 | 等提取完成才返回 | +500ms–5s，严重影响 UX |
| B：Temporal Activity | 用 Temporal 保证可靠执行 | 复杂，过度设计（操作快速幂等） |
| **C：FastAPI BackgroundTasks（采纳）** | 先返回 SSE 流，后台异步执行 | 0 延迟影响 |
| D：消息队列（Kafka/Redis Stream） | 生产环境标准做法 | 引入新依赖，MVP 阶段过重 |

---

## 决策

选择**方案 C：FastAPI `BackgroundTasks`**。

```python
# chat_service.py
async def stream_chat(session_id, query, background_tasks, ...):
    # ... 生成响应 ...
    yield token  # SSE 流式返回

    # 响应完成后，注册后台任务
    background_tasks.add_task(
        memory_manager.extract_and_store,
        session_id, turn_messages, tenant_id, user_id
    )
    background_tasks.add_task(
        memory_manager.maybe_summarize,
        session_id
    )
```

FastAPI BackgroundTasks 在响应完成后、连接关闭前在同一进程内执行，无需额外基础设施。

---

## 后果

**正面**：
- 用户感知零延迟，SSE 流式体验不受影响
- 无需引入消息队列等新组件
- 实现简单，代码直观

**负面**：
- 进程崩溃（极少）会丢失未执行的后台任务，记忆提取可能有遗漏
- 高并发时后台任务与主线程竞争同一事件循环资源
- 无法重试失败的提取任务

**可接受理由**：
- 记忆是"尽力而为"的，偶尔遗漏一次提取不影响功能
- MVP 阶段单机部署，并发压力小
- Phase 12（用量追踪）或更后期，可升级为 Redis Stream / Celery 以获得可靠性保证

---

## 升级路径

```
MVP：FastAPI BackgroundTasks（当前）
  ↓ 如需可靠性
生产：Redis Stream + Worker 异步消费
  ↓ 如需分布式可观测
高级：Temporal Activity（完整 audit trail）
```

---

## 实现备注（2026-04-14）

实际实现采用 `asyncio.create_task()` 而非 `BackgroundTasks`，原因：

`stream_chat` 是异步生成器（`async def ... yield`），`BackgroundTasks` 对象需在路由层注入并透传到生成器内部，造成函数签名污染。`asyncio.create_task()` 在生成器尾部（所有 token 已 yield 后）直接调度，更自然：

```python
# 生成器末尾收集完整响应后触发
async for token in llm.stream_generate(...):
    response_parts.append(token)
    yield token                        # ← token 已流出

asyncio.create_task(_extractor.run(...))  # ← 生成器完成后调度
```

两者可靠性相同（进程崩溃均会丢失任务），行为差异可忽略。

---

## 参考

- `app/services/chat_service.py` — 实现位置（`_stream_with_memory`）
- `app/memory/extractor.py` — `MemoryExtractor.run()`
- ADR-022 — 三层记忆架构整体决策
