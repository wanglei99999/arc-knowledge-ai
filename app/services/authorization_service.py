from app.domain.authorization import (
    AuthorizationError,
    UserContext,
    decorate_access,
    permits,
)
from app.infrastructure.postgres.repositories.authorization_repo import AuthorizationRepository


class AuthorizationService:
    def __init__(self, repo=None):
        self.repo = repo or AuthorizationRepository()

    async def context(self, tenant_id, user_id, membership_id=None):
        if not user_id or not tenant_id:
            raise AuthorizationError(401, "UNAUTHENTICATED", "请先登录")
        row = await self.repo.resolve_context(tenant_id, user_id, membership_id)
        if row is None:
            raise AuthorizationError(403, "TENANT_CONTEXT_INVALID", "当前组织成员身份已失效")
        return UserContext(**row)

    async def list_spaces(self, tenant_id, user_id, *, include_archived=False):
        ctx = await self.context(tenant_id, user_id)
        return [
            decorate_access(row)
            for row in await self.repo.space_access(ctx, include_archived=include_archived)
        ]

    async def authorize_user(
        self, tenant_id, user_id, space_id, action, *, owner=None, allow_archived=False
    ):
        ctx = await self.context(tenant_id, user_id)
        if not space_id:
            raise AuthorizationError(404, "RESOURCE_NOT_FOUND", "必须指定可访问的空间")
        rows = await self.repo.space_access(ctx, space_id, include_archived=allow_archived)
        if not rows:
            raise AuthorizationError(404, "RESOURCE_NOT_FOUND", "资源不存在或无权访问")
        decision = decorate_access(rows[0])
        if not permits(decision["effective_role"], action, ctx.membership_id, owner):
            raise AuthorizationError(403, "PERMISSION_DENIED", "没有执行此操作的权限")
        return {**decision, "membership_id": ctx.membership_id}

    async def authorize_document(self, tenant_id, user_id, document_id, action="space.read"):
        await self.context(tenant_id, user_id)
        document = await self.repo.document_scope(tenant_id, document_id)
        if document is None:
            raise AuthorizationError(404, "RESOURCE_NOT_FOUND", "资源不存在或无权访问")
        decision = await self.authorize_user(
            tenant_id,
            user_id,
            document["space_id"],
            action,
            owner=document["created_by_membership_id"],
        )
        return {**decision, "document": document}

    async def require_admin(self, tenant_id, user_id):
        ctx = await self.context(tenant_id, user_id)
        if ctx.tenant_role != "TENANT_ADMIN":
            raise AuthorizationError(403, "PERMISSION_DENIED", "需要租户管理员权限")
        return ctx

    async def authorize_session(
        self,
        tenant_id,
        user_id,
        session_id=None,
        *,
        turn_id=None,
        space_id=None,
        allow_archived=False,
    ):
        await self.context(tenant_id, user_id)
        scope = await self.repo.session_scope(tenant_id, user_id, session_id, turn_id)
        if scope is None or (space_id is not None and scope["space_id"] != space_id):
            raise AuthorizationError(404, "RESOURCE_NOT_FOUND", "会话不存在或无权访问")
        await self.authorize_user(
            tenant_id, user_id, scope["space_id"], "space.read", allow_archived=allow_archived
        )
        return scope

    @staticmethod
    def document_capabilities(decision, owner):
        return ["document.read"] + [
            action
            for action in ("document.delete", "document.retry")
            if permits(decision["effective_role"], action, decision["membership_id"], owner)
        ]


authorization = AuthorizationService()
