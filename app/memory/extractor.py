"""
MemoryExtractor — LLM 驱动的记忆提取与会话压缩。

两项职责：
1. compress()  — 当消息数超过阈值时，LLM 压缩中间段为摘要（Layer 2）
2. extract()   — 从近期对话提取事实/偏好/目标，ADD/UPDATE/NOOP 写入 Milvus + PG（Layer 3）

后台异步调用，不阻塞 SSE 响应。
"""

from __future__ import annotations

import dataclasses
import json
import logging
import uuid

from app.config.settings import settings
from app.domain.memory import Memory, MemoryCategory, Message
from app.infrastructure.milvus.memory_collection import (
    search_memory_vectors,
    upsert_memory_vector,
)
from app.infrastructure.postgres.repositories.answer_repo import AnswerRepository
from app.infrastructure.postgres.repositories.memory_repo import MemoryRepository
from app.infrastructure.postgres.repositories.session_repo import SessionRepository
from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.pipeline.core.registry import registry
from app.providers.base import ChatMessage, EmbeddingProvider, LLMProvider
from app.providers.llm.model_hub import model_hub

logger = logging.getLogger(__name__)

# ── 提示词 ────────────────────────────────────────────────────────────────────

_COMPRESS_PROMPT = """\
请将以下对话历史压缩为简洁摘要（150 字以内）。
保留：关键决策、重要事实、讨论主题。
忽略：闲聊、重复信息、无关细节。

对话内容：
{conversation}"""

_EXTRACT_PROMPT = """\
从以下对话片段中提取关于用户的重要事实、偏好和目标。
规则：
- 只提取明确陈述的信息，不要推断或猜测
- 每条信息必须具体有价值
- 每行输出一个 JSON 对象，格式：{{"category": "fact|preference|goal", "content": "..."}}
- category 说明：fact=客观事实，preference=用户偏好，goal=用户目标
- 若无值得提取的信息，输出空行即可

对话内容：
{conversation}"""


def _make_ctx(tenant_id: str, llm_provider: str | None = None) -> ProcessingContext:
    """为提取任务创建最小 ProcessingContext（不经过 Hook 检查）"""
    config = TenantConfig(tenant_id=tenant_id)
    if llm_provider:
        config = dataclasses.replace(config, llm_provider=llm_provider)
    return ProcessingContext(
        tenant_id=tenant_id,
        document_id="",
        task_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        quota=QuotaSnapshot(
            max_documents=0,
            max_storage_bytes=0,
            max_api_calls_per_day=999999,
            used_documents=0,
            used_storage_bytes=0,
            used_api_calls_today=0,
        ),
        config=config,
    )


def _compress_threshold(context_window: int) -> int:
    if context_window < 16_000:
        return 20
    elif context_window < 64_000:
        return 80
    return 200


def _format_conversation(messages: list[Message]) -> str:
    return "\n".join(f"{m.role}: {m.content}" for m in messages)


class MemoryExtractor:
    def __init__(
        self,
        *,
        answer_repo: AnswerRepository | None = None,
        session_repo: SessionRepository | None = None,
        memory_repo: MemoryRepository | None = None,
    ) -> None:
        self._answer_repo = answer_repo or AnswerRepository()
        self._session_repo = session_repo or SessionRepository()
        self._memory_repo = memory_repo or MemoryRepository()

    async def persist_answer(
        self,
        *,
        source_turn_id: str | None,
        session_id: str,
        tenant_id: str,
        user_id: str,
        assistant_response: str,
        space_id: str | None,
        citations: list[dict],
    ) -> Message:
        """同步持久化回答关键状态；失败必须向调用方传播。"""
        return await self._answer_repo.save_assistant_with_citations(
            source_turn_id=source_turn_id,
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            content=assistant_response,
            space_id=space_id,
            citations=citations,
        )

    async def run(
        self,
        session_id: str,
        tenant_id: str,
        user_id: str,
        assistant_response: str,
        llm_provider_name: str,
        embedding_provider_name: str,
        space_id: str | None = None,
        citations: list[dict] | None = None,
    ) -> None:
        """普通聊天后台入口：持久化回答后执行非关键记忆工作。"""
        try:
            message = await self.persist_answer(
                source_turn_id=None,
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                assistant_response=assistant_response,
                space_id=space_id,
                citations=citations or [],
            )
        except Exception:
            logger.exception("Background answer persistence failed for session=%s", session_id)
            return
        await self.run_post_persist(
            assistant_message=message,
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            llm_provider_name=llm_provider_name,
            embedding_provider_name=embedding_provider_name,
        )

    async def run_post_persist(
        self,
        *,
        assistant_message: Message,
        session_id: str,
        tenant_id: str,
        user_id: str,
        llm_provider_name: str,
        embedding_provider_name: str,
    ) -> None:
        """回答已落库后的非关键压缩和长期记忆提取。"""
        try:
            session = await self._session_repo.get_by_id(session_id, tenant_id, user_id)
            if session is None:
                return

            # LLM 走 model_hub 统一入口(与检索生成一致):自动获得 Traced 观测 + Resilient 熔断。
            # 此前直接 registry 裸取,记忆链路的 LLM 调用既无 span 也无降级——审查发现 #7。
            ctx = _make_ctx(tenant_id, llm_provider=llm_provider_name)
            llm: LLMProvider = model_hub.get_provider(ctx)
            embed: EmbeddingProvider = registry.get_provider(embedding_provider_name)

            # 1. 压缩检查（Layer 2）
            threshold = _compress_threshold(llm.get_context_window())
            if session.message_count >= threshold:
                await self._compress(
                    session_id=session_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    last_n=settings.memory_last_n,
                    llm=llm,
                    ctx=ctx,
                )

            # 2. 提取记忆（Layer 3）
            await self._extract(
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                last_n=settings.memory_last_n,
                llm=llm,
                embed=embed,
                ctx=ctx,
            )

        except Exception:
            logger.exception("Background memory extraction failed for session=%s", session_id)

    # ── Layer 2: 情节记忆压缩 ─────────────────────────────────────────────────

    async def _compress(
        self,
        session_id: str,
        tenant_id: str,
        user_id: str,
        last_n: int,
        llm: LLMProvider,
        ctx: ProcessingContext,
    ) -> None:
        """压缩 First-2 之后、Last-N 之前的中间段"""
        skip_first = 2
        middle = await self._session_repo.get_middle_messages(
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            skip_first=skip_first,
            skip_last=last_n,
        )
        if not middle:
            return

        conversation = _format_conversation(middle)
        prompt = _COMPRESS_PROMPT.format(conversation=conversation)
        summary = await llm.generate(ctx, [ChatMessage(role="user", content=prompt)])
        await self._session_repo.update_summary(session_id, summary.strip())

    # ── Layer 3: 语义记忆提取 ─────────────────────────────────────────────────

    async def _extract(
        self,
        session_id: str,
        tenant_id: str,
        user_id: str,
        last_n: int,
        llm: LLMProvider,
        embed: EmbeddingProvider,
        ctx: ProcessingContext,
    ) -> None:
        """从最近 last_n 条消息中提取事实"""
        recent = await self._session_repo.get_last_messages(
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            n=last_n,
        )
        if not recent:
            return

        conversation = _format_conversation(recent)
        prompt = _EXTRACT_PROMPT.format(conversation=conversation)
        raw = await llm.generate(ctx, [ChatMessage(role="user", content=prompt)])

        facts = _parse_facts(raw)
        for fact in facts:
            await self._apply_fact(
                fact=fact,
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                embed=embed,
                ctx=ctx,
            )

    async def _apply_fact(
        self,
        fact: dict,
        session_id: str,
        tenant_id: str,
        user_id: str,
        embed: EmbeddingProvider,
        ctx: ProcessingContext,
    ) -> None:
        content = fact["content"]
        try:
            category = MemoryCategory(fact["category"])
        except ValueError:
            category = MemoryCategory.FACT

        # 向量化新事实
        embeddings = await embed.embed(ctx, [content])
        embedding = embeddings[0]

        # 检索最相似的已有记忆
        hits = await search_memory_vectors(
            query_embedding=embedding,
            tenant_id=tenant_id,
            user_id=user_id,
            top_k=1,
            score_threshold=settings.memory_update_threshold,
        )

        if hits:
            # UPDATE：相似记忆已存在，更新内容
            memory_id = hits[0]["memory_id"]
            await self._memory_repo.update_content(memory_id, content)
            await upsert_memory_vector(memory_id, user_id, tenant_id, embedding)
        else:
            # ADD：新记忆
            memory = Memory(
                memory_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                user_id=user_id,
                category=category,
                content=content,
                source_session_id=session_id,
            )
            await self._memory_repo.save(memory)
            await upsert_memory_vector(memory.memory_id, user_id, tenant_id, embedding)


def _parse_facts(raw: str) -> list[dict]:
    facts: list[dict] = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and "category" in obj and "content" in obj:
                facts.append(obj)
        except json.JSONDecodeError:
            pass
    return facts
