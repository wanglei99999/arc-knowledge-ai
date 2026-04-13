# 16 — 全链路追踪 v3（Phase 2）

> **实现版本**：Phase 2（v3.0）
> Phase 1 的链路见 [14-full-flow-v2.md](./14-full-flow-v2.md)。
> 本文描述 Phase 2 新增的两条链路：**搜索** 和 **RAG 问答（SSE）**。

---

## Phase 2 新增的两条链路

```
Phase 1 链路：
HTTP → MinIO 上传 → Temporal → parse → chunk → embed → Milvus + PostgreSQL

Phase 2 新增：
GET /search  → Retrieval Pipeline → Milvus ANN + ES BM25 → RRF → PG 拉文本
POST /chat   → Retrieval Pipeline → RAGOrchestrator → LLM 流式 → SSE
                ↑ 入库侧同步新增 ESIndexStage（写 ES）
```

---

## 链路 A：搜索（GET /search）

### 请求

```
GET /search?q=如何配置多租户隔离&space_id=space-001&top_k=5
Headers: X-Tenant-Id: tenant-abc
```

### 阶段一：API 层 → Service 层

```python
# api/routers/search.py
req = SearchRequest(query="如何配置多租户隔离", tenant_id="tenant-abc", top_k=5)
resp = await _service.search(req)
```

### 阶段二：RAGOrchestrator.retrieve()

```python
# workflows/rag_orchestrator.py

config = TenantConfig(tenant_id="tenant-abc")   # retrieval_strategy="hybrid"
ctx = ProcessingContext.create(tenant_id, document_id="", ...)

strategy = registry.get_strategy("hybrid")       # HybridRetrievalStrategy
pipeline = strategy.build_pipeline("query", config)
# → QueryRewrite → VectorSearch → KeywordSearch → RRFFusion → Rerank

search_ctx = SearchContext(query=RetrievalQuery(
    query_text="如何配置多租户隔离",
    tenant_id="tenant-abc",
    top_k=5,
))
hits = await pipeline.run(ctx, search_ctx)        # → list[SearchHit]
```

### 阶段三：Retrieval Pipeline 内部执行

```
T=0ms    QueryRewriteStage   → pass-through，expanded_queries=[]

T=5ms    VectorSearchStage
         embed("如何配置多租户隔离") → [0.12, -0.34, ...]  (1536维)
         search_vectors(tenant_id="tenant-abc", top_k=5)
         → Milvus HNSW ANN，tenant_id filter
         → vector_hits = [
             {chunk_id: "c-01", score: 0.91, source: "vector"},
             {chunk_id: "c-02", score: 0.88, source: "vector"},
             {chunk_id: "c-05", score: 0.72, source: "vector"},
           ]

T=15ms   KeywordSearchStage
         bm25_search("如何配置多租户隔离", tenant_id="tenant-abc", top_k=5)
         → Elasticsearch BM25
         → keyword_hits = [
             {chunk_id: "c-02", score: 4.2, source: "keyword"},
             {chunk_id: "c-03", score: 3.8, source: "keyword"},
             {chunk_id: "c-01", score: 3.1, source: "keyword"},
           ]

T=16ms   RRFFusionStage（k=60）
         向量排名:  c-01=1, c-02=2, c-05=3
         关键词排名: c-02=1, c-03=2, c-01=3

         RRF 分:
           c-01: 1/61 + 1/63 = 0.0321
           c-02: 1/62 + 1/61 = 0.0323  ← 最高（两路都排前）
           c-03: 0   + 1/62  = 0.0161
           c-05: 1/63 + 0    = 0.0159

         hits = [c-02, c-01, c-03, c-05]（按 RRF 分降序，取 top_k=5）

T=16ms   RerankStage → pass-through（rerank_enabled 但 Provider 未注册）
```

### 阶段四：从 PostgreSQL 拉取 chunk 文本

```python
chunk_ids = ["c-02", "c-01", "c-03", "c-05"]
chunks = await chunk_repo.get_chunks_by_ids(chunk_ids, "tenant-abc")
# → [{chunk_id, content, document_id, chunk_index, ...}, ...]
```

### 最终响应

```json
{
  "query": "如何配置多租户隔离",
  "total": 4,
  "hits": [
    {"chunk_id": "c-02", "score": 0.0323, "source": "keyword", "rank": 1},
    {"chunk_id": "c-01", "score": 0.0321, "source": "vector",  "rank": 2},
    ...
  ],
  "chunks": [
    {"chunk_id": "c-02", "content": "Milvus 通过 tenant_id 作为 Partition Key...", ...},
    ...
  ]
}
```

---

## 链路 B：RAG 问答（POST /chat，SSE 流式）

### 请求

```
POST /chat
Headers: X-Tenant-Id: tenant-abc
Body:
{
  "query": "Milvus 如何实现多租户隔离？",
  "space_id": "space-001",
  "history": [
    {"role": "user", "content": "这个项目用了哪些存储？"},
    {"role": "assistant", "content": "项目使用了 PostgreSQL、Milvus 和 MinIO。"}
  ]
}
```

### 阶段一：API 层返回 StreamingResponse

```python
# api/routers/chat.py

token_stream = _service.stream_chat(req)      # 返回 AsyncIterator[str]
return StreamingResponse(
    _sse_stream(token_stream),                # 包装为 SSE 格式
    media_type="text/event-stream",
    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
)
```

`StreamingResponse` 立即返回 HTTP 200，客户端通过 SSE 接收逐步推送的内容，HTTP 连接保持打开。

### 阶段二：ChatService.stream_chat()

```python
# services/chat_service.py

# 步骤 1：检索（同链路 A，略）
result = await _orchestrator.retrieve(query, tenant_id, top_k, score_threshold)

# 步骤 2：构建历史消息
history = [ChatMessage(role=m["role"], content=m["content"]) for m in req.history]

# 步骤 3：流式生成
async for token in _orchestrator.stream_generate(result, history, tenant_id):
    yield token
```

### 阶段三：RAGOrchestrator._build_messages()

```python
# 系统 Prompt = 模板 + 检索到的 chunk 文本
system = ChatMessage(
    role="system",
    content="""你是一个知识库问答助手。请根据以下检索到的文档片段回答用户问题。

【参考文档】
Milvus 通过 tenant_id 作为 Partition Key 实现多租户物理隔离。
检索时使用 filter=f'tenant_id == "{tenant_id}"' 只在当前租户的 Partition 内搜索...
"""
)

messages = [system, *history, ChatMessage(role="user", content="Milvus 如何实现多租户隔离？")]
```

结构：`system（含检索结果）→ [历史对话] → user（当前问题）`

### 阶段四：LLMProvider.stream_generate()

```python
# providers/llm/openai_llm.py（以 OpenAI 为例）

stream = await self._client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[...],
    stream=True,
)
async for chunk in stream:
    delta = chunk.choices[0].delta.content
    if delta:
        yield delta    # "Milvus" → " 通过" → " Partition" → " Key" → ...
```

### 阶段五：SSE 推送

```python
# api/routers/chat.py

async def _sse_stream(token_iter):
    async for token in token_iter:
        payload = json.dumps({"delta": token}, ensure_ascii=False)
        yield f"data: {payload}\n\n".encode()
    yield b"data: [DONE]\n\n"
```

客户端收到的 SSE 流：
```
data: {"delta": "Milvus"}

data: {"delta": " 通过"}

data: {"delta": " tenant_id"}

data: {"delta": " 作为 Partition Key"}

...

data: [DONE]
```

---

## 完整时间线（Phase 2 问答）

```
T=0ms      POST /chat
T=0ms      StreamingResponse 返回 HTTP 200（连接保持）

（同步处理开始）
T=10ms     RAGOrchestrator.retrieve() 开始
T=20ms     VectorSearchStage：embed 查询 → Milvus ANN
T=35ms     KeywordSearchStage：ES BM25 检索
T=36ms     RRFFusionStage：融合排序
T=50ms     PostgreSQL：拉取 chunk 文本
T=50ms     检索完成，开始调用 LLM

（LLM 流式生成）
T=800ms    首个 token 推送（SSE）
T=850ms    第 2 个 token ...
...
T=3500ms   最后一个 token
T=3501ms   data: [DONE]
T=3501ms   HTTP 连接关闭
```

---

## 与 Phase 1（Ingestion）的关键差异

| 维度 | Phase 1 Ingestion | Phase 2 RAG 问答 |
|------|-------------------|-----------------|
| 编排方式 | Temporal Workflow（异步） | 同步 HTTP（在请求内完成） |
| HTTP 模式 | 202 立即返回 + 轮询 | 200 + SSE 长连接流式 |
| 数据载体 | RawFile → chunks | SearchContext → SearchHits |
| 存储写入 | MinIO + Milvus + PG + ES | 只读（Milvus + ES + PG） |
| 重试保障 | Temporal 断点续跑 | 客户端重新发起请求 |

---

## Phase 3 预告

- `QueryRewriteStage` 接入 LLM 做 HyDE / 多查询扩展
- `RerankStage` 接入 Cohere Rerank 或 BGE-Reranker
- ES analyzer 改为 ik_smart 提升中文分词质量
- ObservabilityHook 接入，记录检索耗时和 LLM token 消耗
