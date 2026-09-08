import pytest


class Relationships:
    def __init__(self, roles=(), active=True, admin=False):
        self.roles = roles
        self.active = active
        self.admin = admin

    async def resolve_context(self, tenant_id, user_id, membership_id=None):
        if not self.active:
            return None
        return dict(
            tenant_id=tenant_id,
            user_id=user_id,
            membership_id="member-1",
            tenant_role="TENANT_ADMIN" if self.admin else "MEMBER",
            email="person@example.com",
            tenant_name="Acme",
        )

    async def space_access(self, ctx, space_id=None, include_archived=False):
        if not self.roles or space_id == "foreign":
            return []
        return [
            dict(
                space_id=space_id or "space-1",
                name="Space",
                space_key="space",
                status="active",
                created_by=None,
                permission_sources=[dict(role=r, subject_type="GROUP") for r in self.roles],
            )
        ]


@pytest.mark.parametrize(
    "roles,action,owner,allowed",
    [
        (["VIEWER"], "space.read", None, True),
        (["VIEWER"], "document.upload", None, False),
        (["VIEWER", "EDITOR"], "document.upload", None, True),
        (["EDITOR"], "document.delete", "member-1", True),
        (["EDITOR"], "document.delete", "someone-else", False),
        (["EDITOR"], "document.retry", None, False),
        (["MANAGER"], "document.delete", "someone-else", True),
        (["MANAGER"], "unknown.action", None, False),
    ],
)
async def test_role_and_creator_determine_actions(roles, action, owner, allowed):
    from app.services.authorization_service import AuthorizationError, AuthorizationService

    service = AuthorizationService(Relationships(roles))
    if allowed:
        decision = await service.authorize_user("tenant", "user", "space", action, owner=owner)
        assert decision["effective_role"] == (
            "MANAGER" if "MANAGER" in roles else "EDITOR" if "EDITOR" in roles else "VIEWER"
        )
    else:
        with pytest.raises(AuthorizationError) as error:
            await service.authorize_user("tenant", "user", "space", action, owner=owner)
        assert error.value.status_code == 403


async def test_admin_has_no_implicit_private_content_access():
    from app.services.authorization_service import AuthorizationError, AuthorizationService

    service = AuthorizationService(Relationships(admin=True))
    with pytest.raises(AuthorizationError) as error:
        await service.authorize_user("tenant", "user", "space", "space.read")
    assert error.value.status_code == 404


async def test_revocation_rechecks_membership_and_grants():
    from app.services.authorization_service import AuthorizationError, AuthorizationService

    repo = Relationships(["EDITOR"])
    service = AuthorizationService(repo)
    await service.authorize_user("tenant", "user", "space", "space.read")
    repo.roles = []
    with pytest.raises(AuthorizationError) as error:
        await service.authorize_user("tenant", "user", "space", "space.read")
    assert error.value.status_code == 404
    repo.active = False
    with pytest.raises(AuthorizationError) as error:
        await service.authorize_user("tenant", "user", "space", "space.read")
    assert error.value.status_code == 403


async def test_resource_in_other_tenant_is_not_discoverable():
    from app.services.authorization_service import AuthorizationError, AuthorizationService

    with pytest.raises(AuthorizationError) as error:
        await AuthorizationService(Relationships(["MANAGER"])).authorize_user(
            "tenant", "user", "foreign", "space.read"
        )
    assert error.value.status_code == 404
