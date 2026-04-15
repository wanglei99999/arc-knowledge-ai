# ADR-024: 动态上下文窗口适配策略

**状态**: 已采纳（待实现）
**日期**: 2026-04-14
**决策人**: 项目团队

---

## 背景

Phase 7 三层记忆系统的 `ContextAssembler` 负责将 system prompt、RAG chunks、近期消息、历史摘要、语义记忆拼装为最终 context。初始设计硬编码 `context_window_tokens = 8192`，存在以下问题：

- 不同 LLM 上下文窗口差异巨大：Ollama 本地模型通常 4k–8k，GPT-4o 128k，Claude 200k
- 硬编码 8192 在大模型上严重浪费容量，在小模型上仍可能溢出
- 摘要压缩触发阈值（消息数 > 20）对大模型意义不大，过早压缩会损失有效对话信息

---

## 备选方案

| 维度 | 方案 A：硬编码 8192 | 方案 B：settings 静态配置 | 方案 C：Provider 动态查询（采纳） |
|------|-------------------|------------------------|-------------------------------|
| 适配不同模型 | 差 | 中（需手动维护） | 好（Provider 自报） |
| 维护成本 | 低 | 中 | 低 |
| 摘要阈值 | 固定 | 可配置但手动 | 按窗口大小自动分档 |
| 预算分配方式 | 绝对值（易溢出） | 绝对值 | 比例制（不会溢出） |

---

## 决策

选择**方案 C：Provider 动态查询 + 比例制预算分配**。

在 `LLMProvider` 基类新增 `get_context_window() -> int` 抽象方法，`ContextAssembler` 运行时调用该接口获取实际窗口大小，所有 token 预算以百分比计算，摘要触发阈值按窗口大小三档自动适配。

---

## 实现细节

### LLMProvider 接口扩展

```python
# providers/base.py
class LLMProvider(ABC):
    @abstractmethod
    def get_context_window(self) -> int:
        """返回模型的上下文窗口大小（tokens）"""
```

实现：
- `OllamaLLMProvider.get_context_window()` → `settings.ollama_context_window`（默认 8192）
- `OpenAILLMProvider.get_context_window()` → 按 model 名映射（gpt-4o=128000 等）

### ContextAssembler 比例分配

| 区域 | 比例 |
|------|------|
| System prompt + First-2 锚点 | 8% |
| RAG chunks | 40% |
| Recent messages（Last-N） | 25% |
| Session summary | 6% |
| Long-term memories | 12% |
| Response buffer | 固定 500 tokens |

### 动态摘要触发阈值

| context_window | 触发阈值 |
|---------------|--------|
| < 16k | 20 条 |
| 16k – 64k | 80 条 |
| > 64k | 200 条 |

---

## 后果

**正面**：
- 大模型充分利用长上下文，小模型自动适配不溢出
- 新增模型只需实现 `get_context_window()`，无需修改 ContextAssembler
- 比例制天然防止某区域独占

**负面**：
- OpenAI 新模型上线需更新 model → context_window 映射表
- 极小 context（2k 以下）下各区域绝对值受限，近期消息可能只放 3–4 条

---

## 参考

- ADR-022 — 三层记忆架构
- ADR-023 — 异步记忆提取
- `app/providers/base.py`
- `app/memory/assembler.py`
