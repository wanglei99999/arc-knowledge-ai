"""
ContextAssembler — 按比例 token 预算组装 LLM 输入。

预算分配（基于模型实际 context_window）：
  System prompt + First-2 锚点  8%
  RAG chunks                   40%
  Recent messages（Last-N）    25%
  Session summary               6%
  Long-term memories           12%
  Response buffer             固定 500 tokens
"""

from __future__ import annotations

from app.domain.memory import Memory, Message, WorkingMemory
from app.providers.base import ChatMessage


def _estimate_tokens(text: str) -> int:
    """粗估 token 数：英文约 4 字符/token，中文约 1.5 字符/token，取均值"""
    return max(1, len(text) // 3)


def _truncate(text: str, max_tokens: int) -> str:
    if _estimate_tokens(text) <= max_tokens:
        return text
    return text[: max_tokens * 3] + "…"


def _trim_messages(messages: list[Message], max_tokens: int) -> list[Message]:
    """从最近的消息向前保留，直到 token 预算用完"""
    kept: list[Message] = []
    used = 0
    for msg in reversed(messages):
        cost = _estimate_tokens(msg.content)
        if used + cost > max_tokens:
            break
        kept.insert(0, msg)
        used += cost
    return kept


def _memories_text(memories: list[Memory], max_tokens: int) -> str:
    lines: list[str] = []
    used = 0
    for mem in memories:
        line = f"- [{mem.category.value}] {mem.content}"
        cost = _estimate_tokens(line)
        if used + cost > max_tokens:
            break
        lines.append(line)
        used += cost
    return "\n".join(lines)


class ContextAssembler:
    """
    将工作记忆、语义记忆、RAG chunks 组装为 LLM 消息列表。

    消息顺序：
      1. system（含语义记忆 + RAG 参考资料）
      2. First-2 锚点消息（若与 recent 有重叠则省略）
      3. [SYSTEM] 对话历史摘要（若有）
      4. Last-N 近期消息
    """

    SYSTEM_PROMPT = (
        "你是一个知识库问答助手。请根据参考资料和用户记忆回答问题，"
        "回答准确、简洁，如果资料中没有相关信息请如实说明，不要编造内容。"
    )
    STRICT_SOURCE_PROMPT = (
        "你是一个附件问答助手。只能依据本轮附件的参考资料得出文档事实，"
        "不得使用模型知识或用户记忆补充附件中没有的信息；证据不足时必须明确说明。"
        "对话历史只用于理解代词、追问和上下文，不得作为附件事实来源。"
    )

    def build(
        self,
        context_window: int,
        working_memory: WorkingMemory,
        semantic_memories: list[Memory],
        rag_text: str,
        strict_sources: bool = False,
    ) -> list[ChatMessage]:
        buffer = 500
        available = context_window - buffer

        budget_rag = int(available * 0.40)
        budget_recent = int(available * 0.25)
        budget_summary = int(available * 0.06)
        budget_memories = int(available * 0.12)

        messages: list[ChatMessage] = []

        # ── 1. System message ─────────────────────────────────────────────────
        system_parts = [self.STRICT_SOURCE_PROMPT if strict_sources else self.SYSTEM_PROMPT]

        mem_text = "" if strict_sources else _memories_text(semantic_memories, budget_memories)
        if mem_text:
            system_parts.append(f"\n\n## 用户记忆\n{mem_text}")

        truncated_rag = _truncate(rag_text, budget_rag) if rag_text else ""
        if truncated_rag:
            system_parts.append(f"\n\n## 参考资料\n{truncated_rag}")

        messages.append(ChatMessage(role="system", content="".join(system_parts)))

        # ── 2. First-2 锚点（仅当 anchor 不在 recent 范围内时追加）──────────
        if working_memory.anchor:
            recent_ids = {m.message_id for m in working_memory.recent}
            truly_anchor = [m for m in working_memory.anchor if m.message_id not in recent_ids]
            for msg in truly_anchor:
                messages.append(ChatMessage(role=msg.role, content=msg.content))

        # ── 3. 情节记忆摘要 ───────────────────────────────────────────────────
        if working_memory.summary:
            truncated_summary = _truncate(working_memory.summary, budget_summary)
            messages.append(
                ChatMessage(
                    role="system",
                    content=f"[历史对话摘要]\n{truncated_summary}",
                )
            )

        # ── 4. Last-N 近期消息 ────────────────────────────────────────────────
        recent = _trim_messages(working_memory.recent, budget_recent)
        for msg in recent:
            messages.append(ChatMessage(role=msg.role, content=msg.content))

        return messages
