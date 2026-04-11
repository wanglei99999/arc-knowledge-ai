# 15 — Retrieval Pipeline：混合检索架构

> **实现版本**：Phase 2（v3.0）
> 对应代码：`app/pipeline/stages/retrieval/` + `app/pipeline/strategies/retrieval/`

## 读完本文你能回答

- Retrieval Pipeline 和 Ingestion Pipeline 有什么结构上的异同？
- `SearchContext` 是什么，为什么需要它？
- 五个 Retrieval Stage 各自做什么？
- RRF 融合算法是怎么工作的？
- 为什么顺序 Pipeline 能处理"看起来需要并行"的两路检索？

---

## 与 Ingestion Pipeline 的对比

Ingestion 和 Retrieval 都复用同一套 `Pipeline` 框架，但数据载体完全不同：

| 维度 | Ingestion Pipeline | Retrieval Pipeline |
|------|--------------------|--------------------|
| 入口数据 | `RawFile` | `SearchContext` |
| 出口数据 | `list[DocumentChunk]` | `list[SearchHit]` |
| 阶段数量 | 5（parse→chunk→embed→milvus→es） | 5（rewrite→vector→keyword→rrf→rerank） |
| 执行时机 | Temporal Activity（异步） | FastAPI 请求内（同步） |
| 延迟要求 | 分钟级可接受 | 秒级（<5s） |

---

## SearchContext：检索 Pipeline 的数据载体

类比 Ingestion 里的 `RawFile → ParsedDocument → list[DocumentChunk]` 链条，Retrieval 用 `SearchContext` 贯穿始终：

```python
@dataclass
class SearchContext:
    query: RetrievalQuery          # 查询信息（含扩展查询）
    vector_hits: list[SearchHit]   # VectorSearchStage 填充
    keyword_hits: list[SearchHit]  # KeywordSearchStage 填充
```

每个 Stage 接收 `SearchContext`，填充自己负责的字段，返回更新后的 `SearchContext`，最后由 `RRFFusionStage` 合并输出 `list[SearchHit]`。

```
SearchContext（空）
    ↓ QueryRewriteStage    → SearchContext（expanded_queries 可选填充）
    ↓ VectorSearchStage    → SearchContext（vector_hits 已填充）
    ↓ KeywordSearchStage   → SearchContext（keyword_hits 已填充）
    ↓ RRFFusionStage       → list[SearchHit]（RRF 融合排序）
    ↓ RerankStage          → list[SearchHit]（精排，当前 pass-through）
```

---

## 五个 Stage 详解

### Stage 1 — QueryRewriteStage（查询改写）

```python
name = "query_rewrite"
Input/Output: SearchContext → SearchContext
```

**当前（Phase 2）**：pass-through，直接返回原 SearchContext。

**Phase 3 扩展**：接入 LLM 做 **HyDE**（Hypothetical Document Embeddings）或多查询扩展：
```
query: "如何配置 Milvus 多租户？"
expanded_queries: [
  "如何配置 Milvus 多租户？",          ← 原始查询
  "Milvus Partition Key 租户隔离",     ← LLM 扩展 1
  "多租户向量数据库最佳实践",           ← LLM 扩展 2
]
```
expanded_queries 非空时，VectorSearchStage 对每条查询分别检索，取最高分合并。

---

### Stage 2 — VectorSearchStage（向量检索）

```python
name = "vector_search"
Input/Output: SearchContext → SearchContext（填充 vector_hits）
```

执行步骤：
1. 从 `ctx.config.embedding_provider` 获取 EmbeddingProvider
2. 对 `expanded_queries`（或原始 `query_text`）批量向量化
3. 调用 `search_vectors()`（Milvus ANN，COSINE + HNSW）
4. 多查询结果合并：相同 chunk_id 取最高分

```python
# 多查询结果去重，保留最高分
for q_text in queries:
    vectors = await provider.embed(ctx, [q_text])
    raw_hits = await search_vectors(vectors[0], tenant_id, top_k, score_threshold)
    for h in raw_hits:
        if h.chunk_id not in best_hits or h.score > best_hits[h.chunk_id].score:
            best_hits[h.chunk_id] = h
```

---

### Stage 3 — KeywordSearchStage（关键词检索）

```python
name = "keyword_search"
Input/Output: SearchContext → SearchContext（填充 keyword_hits）
```

调用 `bm25_search()`，底层是 Elasticsearch：
```json
{
  "query": {
    "bool": {
      "must":   { "match": { "content": "query_text" } },
      "filter": { "term": { "tenant_id": "tenant-abc" } }
    }
  }
}
```

**为什么顺序 Pipeline 能处理两路"并行"检索？**

向量检索和关键词检索看起来应该并行执行，但实际上：
- 两路各自独立，没有数据依赖
- 顺序执行的额外延迟 = BM25 查询时间（通常 < 50ms）
- 实现更简单，不需要 asyncio.gather 管理并发

Phase 3 若延迟敏感，可将 SearchContext 拆分，用 `Pipeline.as_stage()` 嵌套实现并发。

---

### Stage 4 — RRFFusionStage（融合排序）

```python
name = "rrf_fusion"
Input/Output: SearchContext → list[SearchHit]
```

**RRF（Reciprocal Rank Fusion）算法**：

```
score(chunk) = Σ   1 / (k + rank(chunk, list_i))
              list_i
```

- `rank` 从 1 开始（第 1 名得分最高）
- `k = 60`（经验值，平滑参数，防止头部排名过于主导）
- 对 vector_hits 和 keyword_hits 各自按分数排名，再求和

示例（k=60）：

| chunk | 向量排名 | 关键词排名 | RRF 分 |
|-------|---------|----------|--------|
| A | 1 | 3 | 1/61 + 1/63 = 0.032 |
| B | 2 | 1 | 1/62 + 1/61 = 0.032 |
| C | 3 | — | 1/63 + 0 = 0.016 |
| D | — | 2 | 0 + 1/62 = 0.016 |

**优势**：不需要对向量距离和 BM25 分数做归一化（两者尺度完全不同），只用排名位置计算。

---

### Stage 5 — RerankStage（精排）

```python
name = "rerank"
Input/Output: list[SearchHit] → list[SearchHit]
```

**当前（Phase 2）**：pass-through。若 `ctx.config.rerank_enabled = False` 或 RerankProvider 未注册，直接透传。

**Phase 3 扩展**：接入 Cohere Rerank / BGE-Reranker，对 RRF top-K 候选做精排。精排需要 chunk 文本（不只是 ID），届时需在此 Stage 前先拉取文本内容。

---

## 混合检索 vs 单路检索

| 场景 | 纯向量 | 纯 BM25 | 混合 + RRF |
|------|--------|---------|-----------|
| "MinIO 上传文件" | 差（精确名词匹配弱） | 好 | 好 |
| "怎么处理文件损坏" | 好（语义匹配） | 差（词不完全一致） | 好 |
| "ADR-011 说了什么" | 差 | 好 | 好 |
| "文档入库的整体流程" | 好 | 中 | 好 |

混合检索的召回率显著高于单路，是当前 RAG 系统的业界主流方案。

---

## 入库时的双写

Retrieval Pipeline 能运行的前提是入库时已同时写入 Milvus 和 ES：

```
EmbedStage
    ↓
MilvusIndexStage   → Milvus（存向量）
    ↓
ESIndexStage       → Elasticsearch（存原文）
```

两者非原子写入：若 ES 写失败，Milvus 已写成功，该 chunk 在向量检索中可见，但不可被 BM25 检索到。Phase 3 可通过幂等重试或补偿 Job 修复。

---

## 下一步

- 了解 Phase 2 完整请求链路 → [16-full-flow-v3](./16-full-flow-v3.md)
- 了解 RAGOrchestrator 和 SSE 流式生成 → [16-full-flow-v3](./16-full-flow-v3.md) §RAG 生成阶段
