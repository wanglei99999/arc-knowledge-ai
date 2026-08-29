from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.domain.chat_turn import (
    AttachmentStatus,
    AttachmentView,
    ChatTurnView,
    TurnProcessingStatus,
    TurnReadiness,
)
from app.domain.memory import Memory, MemoryCategory, Message, WorkingMemory
from app.domain.retrieval import RetrievalResult, SearchHit
from app.memory.assembler import ContextAssembler
from app.memory.extractor import MemoryExtractor
from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig
from app.services.chat_service import ChatService
from app.services.chat_turn_service import ChatTurnConflictError, ChatTurnService

DOC1 = "11111111-1111-4111-8111-111111111111"


def _attachment() -> AttachmentView:
    return AttachmentView(
        attachment_id="attachment-1",
        client_id="client-1",
        file_name="contract.pdf",
        mime_type="application/pdf",
        file_size=1024,
        document_id=DOC1,
        status=AttachmentStatus.INDEXED,
        ignored=False,
        error_message=None,
    )


def _turn(**overrides) -> ChatTurnView:
    values = {
        "turn_id": "turn-1",
        "session_id": "session-1",
        "space_id": "33333333-3333-4333-8333-333333333333",
        "query": "合同金额是多少？",
        "readiness": TurnReadiness.READY,
        "processing_status": TurnProcessingStatus.WAITING_FILES,
        "processing_error": None,
        "attachments": [_attachment()],
        "assistant": None,
    }
    values.update(overrides)
    return ChatTurnView(**values)


class _TurnRepo:
    def __init__(self, turn: ChatTurnView, claims: list[object] | None = None) -> None:
        self.turn = turn
        self.claims = list(claims or [])
        self.claim_calls: list[dict] = []
        self.failed: list[dict] = []

    async def get_turn(self, **kwargs):
        return self.turn

    async def claim_ready_answer(self, **kwargs):
        self.claim_calls.append(kwargs)
        return self.claims.pop(0) if self.claims else None

    async def fail_answer(self, **kwargs):
        self.failed.append(kwargs)
        return True


@pytest.mark.parametrize("readiness", [TurnReadiness.INGESTING, TurnReadiness.EMPTY])
async def test_answer_rejects_ingesting_or_empty_turn(readiness) -> None:
    repository = _TurnRepo(_turn(readiness=readiness))
    service = ChatTurnService(turn_repo=repository)

    with pytest.raises(ChatTurnConflictError):
        await service.prepare_answer(
            turn_id="turn-1", tenant_id="tenant-1", user_id="user-1"
        )

    assert repository.claim_calls == []


async def test_answer_claim_allows_only_one_concurrent_request() -> None:
    claim = SimpleNamespace(
        turn_id="turn-1",
        session_id="session-1",
        tenant_id="tenant-1",
        user_id="user-1",
        space_id="33333333-3333-4333-8333-333333333333",
        query="合同金额是多少？",
        document_ids=[DOC1],
    )
    repository = _TurnRepo(_turn(), claims=[claim, None])
    service = ChatTurnService(turn_repo=repository)

    prepared = await service.prepare_answer(
        turn_id="turn-1", tenant_id="tenant-1", user_id="user-1"
    )
    with pytest.raises(ChatTurnConflictError, match="状态"):
        await service.prepare_answer(
            turn_id="turn-1", tenant_id="tenant-1", user_id="user-1"
        )

    assert prepared.document_ids == [DOC1]
    assert len(repository.claim_calls) == 2


async def test_answer_uses_only_repository_document_ids() -> None:
    claim = SimpleNamespace(
        turn_id="turn-1",
        session_id="session-1",
        tenant_id="tenant-1",
        user_id="user-1",
        space_id="33333333-3333-4333-8333-333333333333",
        query="合同金额是多少？",
        document_ids=[DOC1],
    )
    stale_attachment = replace(_attachment(), document_id="stale-document-id")
    repository = _TurnRepo(_turn(attachments=[stale_attachment]), claims=[claim])
    service = ChatTurnService(turn_repo=repository)

    prepared = await service.prepare_answer(
        turn_id="turn-1", tenant_id="tenant-1", user_id="user-1"
    )

    assert prepared.document_ids == [DOC1]
    assert "stale-document-id" not in prepared.document_ids


def _working_memory() -> WorkingMemory:
    return WorkingMemory(
        anchor=[],
        summary=None,
        recent=[Message(
            message_id="66666666-6666-4666-8666-666666666666",
            session_id="77777777-7777-4777-8777-777777777777",
            tenant_id="tenant-1",
            user_id="user-1",
            role="user",
            content="合同金额是多少？",
        )],
        message_count=1,
    )


def _processing_context() -> ProcessingContext:
    return ProcessingContext.create(
        tenant_id="tenant-1",
        document_id="",
        quota=QuotaSnapshot(100, 1024, 1000, 0, 0, 0),
        config=TenantConfig(tenant_id="tenant-1"),
    )


class _AnswerManager:
    def __init__(self, *, forbid_save: bool = False, forbid_semantic: bool = False):
        self.forbid_save = forbid_save
        self.forbid_semantic = forbid_semantic

    async def save_user_message(self, *args, **kwargs):
        if self.forbid_save:
            raise AssertionError("附件回答不应重复保存用户消息")

    async def load_semantic_memories(self, *args, **kwargs):
        if self.forbid_semantic:
            raise AssertionError("附件回答不应加载长期语义记忆")
        return []

    async def load_working_memory(self, *args, **kwargs):
        return _working_memory()


class _AnswerLLM:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.messages = []

    def get_context_window(self) -> int:
        return 8192

    async def stream_generate(self, ctx, messages):
        self.messages = messages
        self.events.append("generate")
        yield "金额为 100 万元。"


class _AnswerOrchestrator:
    def __init__(self, events: list[str]) -> None:
        self.retrieval_calls: list[dict] = []
        self.llm = _AnswerLLM(events)

    async def retrieve(self, **kwargs):
        self.retrieval_calls.append(kwargs)
        return RetrievalResult(
            query_text=kwargs["query_text"],
            hits=[SearchHit(
                chunk_id="55555555-5555-4555-8555-555555555555",
                document_id=DOC1,
                chunk_index=0,
                score=0.9,
                source="hybrid",
            )],
            chunks=[{
                "chunk_id": "55555555-5555-4555-8555-555555555555",
                "document_id": DOC1,
                "original_name": "contract.pdf",
                "content": "合同金额为 100 万元",
            }],
        )

    async def build_llm(self, tenant_id, model=None):
        return _processing_context(), self.llm


class _AnswerExtractor:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.persisted: list[dict] = []
        self.post_persisted: list[dict] = []

    async def persist_answer(self, **kwargs):
        self.events.append("persist")
        self.persisted.append(kwargs)
        return Message(
            message_id="88888888-8888-4888-8888-888888888888",
            session_id=kwargs["session_id"],
            tenant_id=kwargs["tenant_id"],
            user_id=kwargs["user_id"],
            role="assistant",
            content=kwargs["assistant_response"],
            created_at=datetime(2026, 8, 29, tzinfo=UTC),
        )

    async def run_post_persist(self, **kwargs):
        self.post_persisted.append(kwargs)


def _answer_request():
    return SimpleNamespace(
        turn_id="turn-1",
        session_id="77777777-7777-4777-8777-777777777777",
        tenant_id="tenant-1",
        user_id="user-1",
        space_id="33333333-3333-4333-8333-333333333333",
        query="合同金额是多少？",
        document_ids=[DOC1],
        top_k=10,
        score_threshold=0.5,
        model=None,
        llm_provider_name="openai_llm",
        embedding_provider_name="openai_embedding",
    )


async def _collect_attachment_answer(*, manager: _AnswerManager):
    events: list[str] = []
    orchestrator = _AnswerOrchestrator(events)
    extractor = _AnswerExtractor(events)
    service = ChatService(
        orchestrator=orchestrator,
        manager=manager,
        assembler=ContextAssembler(),
        extractor=extractor,
    )
    output = []
    async for item in service.stream_attachment_answer(_answer_request()):
        events.append("consumer-citations" if isinstance(item, list) else "consumer-token")
        output.append(item)
    await asyncio.sleep(0)
    return output, events, orchestrator, extractor


async def test_attachment_answer_does_not_save_user_message_twice() -> None:
    output, _, _, _ = await _collect_attachment_answer(
        manager=_AnswerManager(forbid_save=True)
    )

    assert output[0] == "金额为 100 万元。"


async def test_attachment_answer_omits_semantic_memory_lookup() -> None:
    output, _, _, _ = await _collect_attachment_answer(
        manager=_AnswerManager(forbid_semantic=True)
    )

    assert output[0] == "金额为 100 万元。"


async def test_attachment_answer_scopes_retrieval_and_persists_before_citations() -> None:
    output, events, orchestrator, extractor = await _collect_attachment_answer(
        manager=_AnswerManager()
    )

    assert orchestrator.retrieval_calls[0]["document_ids"] == [DOC1]
    assert isinstance(output[-1], list)
    assert events.index("persist") < events.index("consumer-citations")
    assert extractor.persisted[0]["source_turn_id"] == "turn-1"
    assert extractor.post_persisted[0]["assistant_message"].role == "assistant"


def test_attachment_answer_uses_strict_source_prompt() -> None:
    memory = Memory(
        memory_id="99999999-9999-4999-8999-999999999999",
        tenant_id="tenant-1",
        user_id="user-1",
        category=MemoryCategory.FACT,
        content="其他项目的金额是 200 万元",
    )

    messages = ContextAssembler().build(
        context_window=8192,
        working_memory=_working_memory(),
        semantic_memories=[memory],
        rag_text="合同金额为 100 万元",
        strict_sources=True,
    )

    assert "只能依据本轮附件" in messages[0].content
    assert "证据不足" in messages[0].content
    assert "其他项目的金额" not in messages[0].content


async def test_memory_extractor_persist_answer_delegates_to_atomic_repository() -> None:
    calls: list[dict] = []

    class _AnswerRepo:
        async def save_assistant_with_citations(self, **kwargs):
            calls.append(kwargs)
            return Message(
                message_id="88888888-8888-4888-8888-888888888888",
                session_id=kwargs["session_id"],
                tenant_id=kwargs["tenant_id"],
                user_id=kwargs["user_id"],
                role="assistant",
                content=kwargs["content"],
            )

    extractor = MemoryExtractor(answer_repo=_AnswerRepo())
    message = await extractor.persist_answer(
        source_turn_id="turn-1",
        session_id="77777777-7777-4777-8777-777777777777",
        tenant_id="tenant-1",
        user_id="user-1",
        assistant_response="回答",
        space_id="33333333-3333-4333-8333-333333333333",
        citations=[],
    )

    assert message.role == "assistant"
    assert calls[0]["source_turn_id"] == "turn-1"
    assert calls[0]["content"] == "回答"


class _AttachmentChat:
    def __init__(self, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.requests = []

    async def stream_attachment_answer(self, request):
        self.requests.append(request)
        yield "首个 token"
        if self.failure is not None:
            raise self.failure
        yield [{"doc_id": DOC1}]


def _claim():
    return SimpleNamespace(
        turn_id="turn-1",
        session_id="77777777-7777-4777-8777-777777777777",
        tenant_id="tenant-1",
        user_id="user-1",
        space_id="33333333-3333-4333-8333-333333333333",
        query="合同金额是多少？",
        document_ids=[DOC1],
    )


async def test_stream_answer_passes_trusted_claim_to_attachment_chat() -> None:
    repository = _TurnRepo(_turn())
    chat = _AttachmentChat()
    service = ChatTurnService(turn_repo=repository, chat_service=chat)

    output = [item async for item in service.stream_answer(_claim())]

    assert output == ["首个 token", [{"doc_id": DOC1}]]
    assert chat.requests[0].document_ids == [DOC1]
    assert chat.requests[0].query == "合同金额是多少？"
    assert repository.failed == []


async def test_stream_failure_marks_answer_failed_with_safe_message() -> None:
    repository = _TurnRepo(_turn())
    chat = _AttachmentChat(RuntimeError("secret upstream credential"))
    service = ChatTurnService(turn_repo=repository, chat_service=chat)

    with pytest.raises(ChatTurnConflictError, match="回答生成失败，请重试"):
        _ = [item async for item in service.stream_answer(_claim())]

    assert repository.failed[0]["error_message"] == "回答生成失败，请重试"


async def test_stream_cancellation_marks_answer_failed_and_reraises() -> None:
    repository = _TurnRepo(_turn())
    chat = _AttachmentChat(asyncio.CancelledError())
    service = ChatTurnService(turn_repo=repository, chat_service=chat)

    with pytest.raises(asyncio.CancelledError):
        _ = [item async for item in service.stream_answer(_claim())]

    assert repository.failed[0]["error_message"] == "回答连接已中断，请重试"


async def test_consumer_closing_stream_marks_answer_failed() -> None:
    repository = _TurnRepo(_turn())
    chat = _AttachmentChat()
    service = ChatTurnService(turn_repo=repository, chat_service=chat)
    stream = service.stream_answer(_claim())

    assert await anext(stream) == "首个 token"
    await stream.aclose()

    assert repository.failed[0]["error_message"] == "回答连接已中断，请重试"
