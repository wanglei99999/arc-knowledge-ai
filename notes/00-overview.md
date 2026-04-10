# 00 — 架构全貌

## 🎯 读完本文你能回答

- 整个系统分几层？每层干什么？
- 为什么不用一个大函数写完？
- "工业级"和"玩具级"的本质区别在哪？

---

## 问题的起点

假设你要做一个功能：用户上传 PDF，系统把它拆成小段，转成向量，存进数据库，以后可以搜索。

**玩具写法**：

```python
def process(file_path):
    text = extract_text(file_path)      # 解析
    chunks = split(text, size=512)       # 切片
    vectors = openai.embed(chunks)       # 向量化
    db.insert(chunks, vectors)           # 写库
```

这样写能跑，但有以下问题：

| 问题 | 现象 |
|------|------|
| 崩溃无法恢复 | 向量化到一半断了，只能从头重来 |
| 无法换模型 | OpenAI 换成 Ollama 要改函数内部 |
| 无法多租户 | 加一个参数 tenant_id 传来传去 |
| 无法观测 | 哪步慢了？哪步错了？不知道 |
| 无法扩展 | 加一个 OCR 步骤要改函数本体 |

工业级架构就是**把这些问题提前设计好**。

---

## 六层架构

```
┌──────────────────────────────────────┐
│  Layer 6: API / gRPC                  │  ← 只做协议转换（HTTP → 内部命令）
├──────────────────────────────────────┤
│  Layer 5: Service                     │  ← 业务用例（ingest / search / chat）
├──────────────────────────────────────┤
│  Layer 4: Workflow（Temporal）         │  ← 长流程编排，管重试和 Checkpoint
├──────────────────────────────────────┤
│  Layer 3: Pipeline / Stage / Hook     │  ← 执行流水线（核心框架）
├──────────────────────────────────────┤
│  Layer 2: Provider                    │  ← 可替换的 AI 能力（解析/嵌入/LLM）
├──────────────────────────────────────┤
│  Layer 1: Infrastructure              │  ← PG / Milvus / Redis / MinIO
└──────────────────────────────────────┘
```

**唯一规则**：上层可以调用下层，下层**绝不**反向依赖上层。

---

## 六个核心抽象

| 抽象 | 类比 | 一句话 |
|------|------|-------|
| `ProcessingContext` | 快递单 | 随请求流动的所有信息 |
| `BaseStage` | 流水线工位 | 做一件事，不知道前后是谁 |
| `Pipeline` | 流水线传送带 | 把 Stage 串起来按顺序执行 |
| `BaseHook` | 质检插槽 | 在 Stage 前后注入横切能力 |
| `BaseProvider` | 外包供应商 | 具体 AI 能力的可替换实现 |
| `ComponentRegistry` | 工厂目录 | 按名字找到对应实现类 |

---

## 当前实现的唯一链路

Phase 0 只实现了**文档入库**这一条路：

```
POST /documents/upload
       │
       ▼
DocumentService.ingest()       → 生成 document_id，触发 Temporal
       │
       ▼
IngestionWorkflow（Temporal）   → 管重试，提供 Checkpoint
       │
   ┌───┼───┐
   ▼   ▼   ▼
 parse chunk embed             → 三个独立 Activity，各自重试
   │   │   │
   ▼   ▼   ▼
Parser Chunker Embedder        → 三个 Stage，各自专注
   │       │
   ▼       ▼
Unstructured OpenAI            → 两个 Provider，可替换
               │
               ▼
          PostgreSQL           → 最终存储
```

---

## 设计的核心思想

**变化发生在边缘，核心保持稳定**

```
变化快的：Provider（今天 OpenAI，明天 Ollama）
变化中的：Strategy（不同租户用不同处理方案）
变化慢的：Pipeline 框架（Stage / Hook / Registry）
几乎不变：ProcessingContext 和 BaseStage 接口
```

新增一个 OCR 能力 = 写一个 `PaddleOCRProvider` + 注册。不需要改 Pipeline 框架。

---

## 下一步

- 想理解数据怎么定义 → [01 领域模型](./01-domain-model.md)
- 想理解请求信息怎么流动 → [02 Context](./02-context.md)
- 想直接看完整链路 → [11 完整链路追踪](./11-full-flow.md)
