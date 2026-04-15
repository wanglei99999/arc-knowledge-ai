# ADR-021: 文档删除不使用 Temporal，采用直接顺序异步调用

**状态**: 已采纳
**日期**: 2026-04-13
**决策人**: 项目团队

---

## 背景

Phase 6 实现文档删除时，初版设计（PROGRESS.md Phase 6 草稿）计划通过 Temporal `delete_activity` 保证"四处清理的原子性"。需要决定是否真的需要 Temporal。

文档入库（Ingestion）使用 Temporal 的原因：
1. 流程耗时长（解析→切片→向量化可能数分钟）
2. 中间有 CPU/IO 密集型 Activity，需要 Worker 分布式执行
3. 需要持久化进度，中断后可恢复

---

## 备选方案

| 维度 | 方案 A：Temporal Workflow | 方案 B：直接顺序 async（采纳） |
|------|---------|---------|
| 操作耗时 | N/A | 四个操作均为毫秒级网络调用 |
| 幂等性 | 内建 | 天然幂等（删除不存在的数据不报错） |
| 代码复杂度 | 高（需 Workflow + Activity + Worker 注册） | 低（service 层顺序 await） |
| 失败重试 | 自动（Activity 级别） | 需要手动重试，或前端重新发 DELETE 请求 |
| 进度可见 | 可查询 Workflow 状态 | status=DELETING 可作进度指示 |
| 适用场景 | 长流程、需要 Worker 的 CPU 密集任务 | 快速、幂等、顺序的网络调用 |

---

## 决策

选择**方案 B：直接顺序 async 调用**。

文档删除的四个操作（MinIO/Milvus/ES/PG）均为快速幂等的网络调用，引入 Temporal 的复杂度远超收益。`status=DELETING` 标志提供足够的并发保护，中途失败可直接重试 DELETE 请求。

---

## 删除实现

```python
# DocumentService.delete()
await repo.update_document_status(..., DELETING)   # 加锁
await minio_delete_file(meta["file_path"])          # 物理删原文件
await milvus_delete_by_document(doc_id, tenant_id) # 物理删向量
await es_delete_by_document(doc_id, tenant_id)     # 物理删索引
await repo.delete_chunks_by_document(doc_id, tenant_id)  # 物理删 chunks
await repo.update_document_status(..., DELETED)    # 逻辑删文档记录
```

---

## 后果

**正面**：
- 实现简单，无需新增 Temporal Workflow / Activity 注册
- 删除接口响应快（全部 < 1s）
- 幂等：任何一步失败重试 DELETE，已删除的存储不会报错

**负面**：
- 中途失败（如 Milvus 超时后进程崩溃）会留下部分删除状态（status=DELETING），需要运维或 Phase 9 的测试覆盖修复路径
- 无 Temporal 的自动 Activity 重试，网络抖动需调用方重试

**可接受理由**：MVP 阶段删除操作频率低，部分删除状态可手动修复，不值得引入 Temporal 复杂度。

---

## 参考

- `app/services/document_service.py` — `delete()` 方法实现
- ADR-020 — 文档删除混合策略
- PROGRESS.md §Phase 6 — 实际调用链路
