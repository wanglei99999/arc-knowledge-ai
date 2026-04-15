# ADR-020: 文档删除采用混合删除策略

**状态**: 已采纳
**日期**: 2026-04-13
**决策人**: 项目团队

---

## 背景

Phase 6 需要实现文档删除能力。文档数据分散在四个存储层：
- PostgreSQL `documents` 表（元数据）
- PostgreSQL `document_chunk` 表（分片文本）
- MinIO（原始文件）
- Milvus（向量数据）
- Elasticsearch（全文索引）

需要决定每一层使用物理删除还是逻辑删除。

---

## 备选方案

| 维度 | 方案 A：全部物理删除 | 方案 B：全部逻辑删除 | 方案 C：混合删除（采纳） |
|------|---------|---------|---------|
| 审计追踪 | 无法追溯谁在何时删了什么 | 所有层均保留 | documents 记录保留 |
| 误删恢复 | 不可恢复 | 可完全恢复 | 可恢复元数据，重新入库即可恢复 |
| 存储成本 | 最低 | 最高（大量"已删除"数据占用向量/文件存储） | 仅保留轻量元数据行 |
| 查询过滤 | 不需要 | 全部查询需加 `status != DELETED` 过滤 | 仅 documents 表需过滤 |
| 合规要求 | 无法满足 | 满足 | 满足（保留操作记录） |

---

## 决策

选择**方案 C：混合删除**。

`documents` 表记录逻辑删除（`status=DELETED`），保留审计信息；MinIO 文件、Milvus 向量、ES 索引、PG chunks 全部物理删除，因为这些是派生数据，存储成本高且无独立业务价值。

---

## 实现细节

### 删除流程

```
DELETE /documents/{id}
  1. 查 documents 元数据（获取 file_path / 校验存在）
  2. 标记 status=DELETING（防并发重复删除）
  3. 物理删除 MinIO 原始文件
  4. 物理删除 Milvus 向量
  5. 物理删除 ES 索引
  6. 物理删除 PG document_chunk 记录
  7. 标记 status=DELETED（逻辑删除）
```

### 新增状态

`DocumentStatus` 新增两个枚举值：
- `DELETING = "deleting"` — 删除进行中，防并发
- `DELETED = "deleted"` — 已逻辑删除

### 状态转换

所有非终态（INDEXED / STALE / FAILED）均可转入 DELETING，DELETING 唯一合法后继为 DELETED。

---

## 后果

**正面**：
- `documents` 表保留完整审计记录（谁、何时、删了什么）
- 存储成本低（向量/文件/全文索引不保留）
- 查询过滤简单（只有 documents 表需要过滤 DELETED 状态）
- 可选：90 天后定时任务物理清理 documents 记录满足合规

**负面**：
- 误删文档无法原地恢复（需重新上传入库），只能恢复元数据记录
- `status=DELETING` 中途失败时需要手动或幂等重试机制（Phase 9 测试覆盖）

---

## 参考

- `app/domain/document.py` — DocumentStatus 状态机
- `app/infrastructure/postgres/repositories/chunk_repo.py` — `get_document_meta()` / `delete_chunks_by_document()`
- ADR-021 — 为何不使用 Temporal 处理删除
