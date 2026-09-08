"""Real PostgreSQL security tests, confined to an ephemeral, uniquely named schema.

Never initialize/reset public. Infrastructure refusal skips; SQL/policy failures fail.
"""

import re
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config.settings import settings
from app.domain.authorization import AuthorizationError
from app.domain.user import User
from app.infrastructure.postgres import client as postgres
from app.infrastructure.postgres.repositories.chunk_repo import ChunkRepository
from app.infrastructure.postgres.repositories.session_repo import SessionRepository
from app.infrastructure.postgres.repositories.user_repo import UserRepository
from app.services.access_management_service import AccessManagementService
from app.services.authorization_service import authorization
from scripts.migrate import DDL

_unavailable = False


@pytest.fixture
async def database(monkeypatch):
    global _unavailable
    if _unavailable:
        pytest.skip("Configured PostgreSQL unavailable; no database mutation attempted")
    try:
        conn = await asyncpg.connect(
            settings.postgres_url.replace("postgresql+asyncpg://", "postgresql://"), timeout=3
        )
    except (OSError, TimeoutError):
        _unavailable = True
        pytest.skip(
            "Configured PostgreSQL connection refused/timed out; no database mutation attempted"
        )
    schema = "auth_test_" + uuid4().hex
    assert re.fullmatch(r"auth_test_[0-9a-f]{32}", schema)
    engine = None
    try:
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        await conn.execute(f'SET search_path TO "{schema}"')
        await conn.execute(DDL)
        await conn.execute(DDL)  # Fresh initialization is idempotent.
        engine = create_async_engine(
            settings.postgres_url,
            connect_args={"server_settings": {"search_path": schema}},
            echo=False,
        )
        monkeypatch.setattr(
            postgres, "_SessionFactory", async_sessionmaker(engine, expire_on_commit=False)
        )
        yield conn
    finally:
        if engine:
            await engine.dispose()
        # Only the exact schema created by this fixture is ever dropped.
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await conn.close()


async def organization(email="owner@example.com"):
    user = User(email=email, hashed_password="test-hash-not-used-for-login")
    await UserRepository().register_organization(user, "Test tenant")
    membership = (await UserRepository().memberships(user.user_id))[0]
    ctx = await authorization.context(membership["tenant_id"], user.user_id)
    spaces = await authorization.list_spaces(ctx.tenant_id, ctx.user_id)
    return ctx, spaces


async def expect_denied(awaitable, code=404):
    with pytest.raises(AuthorizationError) as error:
        await awaitable
    assert error.value.status_code == code


async def test_fresh_registration_and_cross_tenant_foreign_keys(database):
    first, spaces = await organization()
    second, _ = await organization("other@example.com")
    assert len(spaces) == 2
    assert {s["visibility_summary"] for s in spaces} == {"public", "private"}
    assert all(s["effective_role"] == "MANAGER" for s in spaces)
    assert await database.fetchval("SELECT max(version) FROM schema_versions") == 4
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await database.execute(
            "INSERT INTO space_grants(tenant_id,space_id,subject_type,tenant_membership_id,role,created_by_membership_id) VALUES ($1,$2,'MEMBER',$3,'VIEWER',$4)",
            first.tenant_id,
            spaces[0]["space_id"],
            second.membership_id,
            first.membership_id,
        )
    await expect_denied(
        authorization.authorize_user(
            second.tenant_id, second.user_id, spaces[0]["space_id"], "space.read"
        )
    )


async def test_group_ancestors_union_and_revocation(database):
    admin, _ = await organization()
    outside, _ = await organization("employee@example.com")
    management = AccessManagementService()
    member = await management.add_member(admin, outside.email, "MEMBER")
    employee = await authorization.context(admin.tenant_id, outside.user_id)
    parent = await management.save_group(admin, name="Parent")
    child = await management.save_group(admin, name="Child", parent_group_id=parent["id"])
    other = await management.save_group(admin, name="Other")
    await management.group_members(admin, child["id"], membership_id=member["membership_id"])
    await management.group_members(admin, other["id"], membership_id=member["membership_id"])
    space = await management.create_space(admin, "Team", "group", parent["id"], "VIEWER")
    sid = space["space_id"]
    assert (
        await authorization.authorize_user(admin.tenant_id, employee.user_id, sid, "space.read")
    )["effective_role"] == "VIEWER"
    await management.put_grant(admin, sid, "GROUP", "EDITOR", group_id=other["id"])
    assert (
        await authorization.authorize_user(
            admin.tenant_id, employee.user_id, sid, "document.upload"
        )
    )["effective_role"] == "EDITOR"
    await management.group_members(
        admin, other["id"], membership_id=member["membership_id"], remove=True
    )
    await expect_denied(
        authorization.authorize_user(admin.tenant_id, employee.user_id, sid, "document.upload"), 403
    )
    await management.group_members(
        admin, child["id"], membership_id=member["membership_id"], remove=True
    )
    await expect_denied(
        authorization.authorize_user(admin.tenant_id, employee.user_id, sid, "space.read")
    )
    await management.group_members(admin, parent["id"], membership_id=member["membership_id"])
    child_space = await management.create_space(admin, "Child only", "group", child["id"])
    await expect_denied(
        authorization.authorize_user(
            admin.tenant_id, employee.user_id, child_space["space_id"], "space.read"
        )
    )
    await expect_denied(
        management.save_group(admin, group_id=parent["id"], parent_group_id=child["id"]), 409
    )


async def test_last_manager_and_last_admin_rollback(database):
    admin, spaces = await organization()
    sid = next(s["space_id"] for s in spaces if s["visibility_summary"] == "private")
    management = AccessManagementService()
    grant = (await management.grants(admin, sid))[0]
    await expect_denied(management.revoke_grant(admin, sid, grant["id"]), 409)
    assert (
        await authorization.authorize_user(admin.tenant_id, admin.user_id, sid, "space.manage")
    )["effective_role"] == "MANAGER"
    await expect_denied(
        management.put_grant(
            admin, sid, "MEMBER", "VIEWER", tenant_membership_id=admin.membership_id
        ),
        409,
    )
    await expect_denied(management.update_member(admin, admin.membership_id, role="MEMBER"), 409)
    assert (
        await authorization.context(admin.tenant_id, admin.user_id)
    ).tenant_role == "TENANT_ADMIN"


async def test_takeover_audits_reason_but_does_not_grant_session_ownership(database):
    admin, _ = await organization()
    outside, _ = await organization("employee@example.com")
    management = AccessManagementService()
    member = await management.add_member(admin, outside.email, "MEMBER")
    employee = await authorization.context(admin.tenant_id, outside.user_id)
    personal = next(
        s
        for s in await authorization.list_spaces(employee.tenant_id, employee.user_id)
        if s["visibility_summary"] == "private"
    )
    sid = personal["space_id"]
    await expect_denied(
        authorization.authorize_user(admin.tenant_id, admin.user_id, sid, "space.read")
    )
    session = await SessionRepository().create(employee.tenant_id, employee.user_id, space_id=sid)
    await expect_denied(management.takeover(admin, sid, "  "), 422)
    await management.takeover(admin, sid, "Employee handover")
    await authorization.authorize_user(admin.tenant_id, admin.user_id, sid, "space.manage")
    await expect_denied(
        authorization.authorize_session(admin.tenant_id, admin.user_id, session.session_id)
    )
    audits = await management.audit_events(admin)
    assert any(
        a["action"] == "space.takeover" and a["metadata"]["reason"] == "Employee handover"
        for a in audits
    )
    await management.update_member(admin, member["membership_id"], status="suspended")
    await expect_denied(authorization.context(employee.tenant_id, employee.user_id), 403)


async def test_chunk_hydration_never_returns_another_space(database):
    admin, spaces = await organization()
    repo = ChunkRepository()
    ids = []
    for index, space in enumerate(spaces):
        did, cid = str(uuid4()), str(uuid4())
        ids.append(cid)
        await repo.create_document(
            did,
            admin.tenant_id,
            space["space_id"],
            "secret.txt",
            "text/plain",
            "not-uploaded",
            admin.membership_id,
        )
        await database.execute(
            "INSERT INTO document_chunks(chunk_id,document_id,tenant_id,content,chunk_index) VALUES ($1,$2,$3,$4,0)",
            cid,
            did,
            admin.tenant_id,
            f"space-{index}",
        )
    result = await repo.get_chunks_by_ids(ids, admin.tenant_id, spaces[0]["space_id"])
    assert [r["content"] for r in result] == ["space-0"]


async def test_derived_rows_reject_cross_tenant_parent_relationships(database):
    first, first_spaces = await organization()
    second, second_spaces = await organization("second@example.com")
    document_id = str(uuid4())
    await ChunkRepository().create_document(
        document_id,
        first.tenant_id,
        first_spaces[0]["space_id"],
        "first.txt",
        "text/plain",
        "not-uploaded",
        first.membership_id,
    )
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await database.execute(
            "INSERT INTO document_chunks(chunk_id,document_id,tenant_id,content,chunk_index) "
            "VALUES ($1,$2,$3,'cross tenant',0)",
            str(uuid4()),
            document_id,
            second.tenant_id,
        )

    session = await SessionRepository().create(
        first.tenant_id, first.user_id, space_id=first_spaces[0]["space_id"]
    )
    message = await SessionRepository().add_message(
        session.session_id,
        first.tenant_id,
        first.user_id,
        "user",
        "valid message",
    )
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await database.execute(
            "INSERT INTO messages(message_id,session_id,tenant_id,user_id,role,content) "
            "VALUES ($1,$2,$3,$4,'user','cross tenant')",
            str(uuid4()),
            session.session_id,
            second.tenant_id,
            second.user_id,
        )

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await database.execute(
            "INSERT INTO message_attachments(attachment_id,message_id,tenant_id,space_id,"
            "client_id,document_id,file_name,mime_type,file_size) "
            "VALUES ($1,$2,$3,$4,'cross-space',$5,'first.txt','text/plain',1)",
            str(uuid4()),
            message.message_id,
            first.tenant_id,
            first_spaces[1]["space_id"],
            document_id,
        )

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await database.execute(
            "INSERT INTO message_attachments(attachment_id,message_id,tenant_id,space_id,"
            "client_id,file_name,mime_type,file_size) "
            "VALUES ($1,$2,$3,$4,'cross-tenant-space','pending.txt','text/plain',1)",
            str(uuid4()),
            message.message_id,
            first.tenant_id,
            second_spaces[0]["space_id"],
        )

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await database.execute(
            "INSERT INTO message_citations(id,message_id,tenant_id,space_id,content_snapshot) "
            "VALUES ($1,$2,$3,$4,'cross tenant')",
            str(uuid4()),
            message.message_id,
            second.tenant_id,
            second_spaces[0]["space_id"],
        )

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await database.execute(
            "INSERT INTO memories(memory_id,tenant_id,user_id,space_id,category,content,"
            "source_session_id) VALUES ($1,$2,$3,$4,'fact','cross tenant',$5)",
            str(uuid4()),
            second.tenant_id,
            second.user_id,
            second_spaces[0]["space_id"],
            session.session_id,
        )

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await database.execute(
            "INSERT INTO sessions(session_id,tenant_id,user_id,space_id) VALUES ($1,$2,$3,$4)",
            str(uuid4()),
            second.tenant_id,
            first.user_id,
            second_spaces[0]["space_id"],
        )
