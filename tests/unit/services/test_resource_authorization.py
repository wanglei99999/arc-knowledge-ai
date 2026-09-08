"""Removing any service entry check must expose the guarded downstream operation."""

from types import SimpleNamespace

import pytest

from app.domain.authorization import AuthorizationError
from app.services.authorization_service import authorization


@pytest.fixture
def denied(monkeypatch):
    async def deny(*args, **kwargs):
        raise AuthorizationError(404, "not_found", "Resource not found")

    monkeypatch.setattr(authorization, "authorize_user", deny)
    monkeypatch.setattr(authorization, "authorize_document", deny)
    monkeypatch.setattr(authorization, "authorize_session", deny)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["delete", "retry_ingestion", "get_status", "list_documents", "get_content"]
)
async def test_document_denial_precedes_repository_and_storage(denied, monkeypatch, operation):
    from app.services import document_service as module

    def forbidden_repo():
        pytest.fail("denied request reached document repository")

    monkeypatch.setattr(module, "ChunkRepository", forbidden_repo)
    service = module.DocumentService()
    with pytest.raises(AuthorizationError):
        if operation == "list_documents":
            await service.list_documents("tenant", "space", user_id="user")
        else:
            await getattr(service, operation)("document", "tenant", user_id="user")


@pytest.mark.asyncio
async def test_search_denial_precedes_retrieval(denied, monkeypatch):
    from app.services import retrieval_service as module

    async def forbidden(**kwargs):
        pytest.fail("denied search reached retrieval")

    monkeypatch.setattr(module._orchestrator, "retrieve", forbidden)
    with pytest.raises(AuthorizationError):
        await module.RetrievalService().search(
            module.SearchRequest(
                query="secret", tenant_id="tenant", space_id="space", user_id="user"
            )
        )


@pytest.mark.asyncio
async def test_session_creation_denial_precedes_insert(denied):
    from app.services.session_service import SessionService

    async def forbidden(*args, **kwargs):
        pytest.fail("denied request created a session")

    service = SessionService(session_repo=SimpleNamespace(create=forbidden))
    with pytest.raises(AuthorizationError):
        await service.create("tenant", "user", space_id="space")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["get", "delete", "archive", "restore", "pin", "unpin", "rename", "get_messages"]
)
async def test_revoked_session_denies_history_and_mutations(denied, operation):
    from app.services.session_service import SessionService

    class NoReads:
        def __getattr__(self, name):
            pytest.fail(f"denied session touched repository: {name}")

    service = SessionService(session_repo=NoReads())
    with pytest.raises(AuthorizationError):
        args = ["session", "tenant", "user"]
        if operation == "rename":
            args.append("title")
        await getattr(service, operation)(*args)


@pytest.mark.asyncio
async def test_revoked_chat_denies_before_any_stream_output(denied):
    from app.services.chat_service import ChatRequest, ChatService

    with pytest.raises(AuthorizationError):
        async for item in ChatService().stream_chat(
            ChatRequest(
                query="secret",
                tenant_id="tenant",
                user_id="user",
                space_id="space",
                session_id="session",
            )
        ):
            pytest.fail("denied stream emitted data")


@pytest.mark.asyncio
async def test_revoked_turn_denies_before_attachment_and_answer_snapshots(denied):
    from app.services.chat_turn_service import ChatTurnService

    class NoReads:
        def __getattr__(self, name):
            pytest.fail(f"denied turn read {name}")

    with pytest.raises(AuthorizationError):
        await ChatTurnService(turn_repo=NoReads()).get_turn(
            turn_id="turn", tenant_id="tenant", user_id="user"
        )
