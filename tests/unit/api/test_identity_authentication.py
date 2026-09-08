"""Authentication boundary regressions using real JWT decoding and auth services."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from app.api import dependencies
from app.api.routers import auth
from app.config.settings import settings
from app.domain.authorization import AuthorizationError
from app.domain.user import User
from app.services import auth_service
from app.services.auth_service import AuthService
from app.services.authorization_service import AuthorizationService


class IdentityStore:
    def __init__(self):
        self.user = User(
            email="person@example.com", hashed_password=AuthService()._hash_password("password123")
        )
        self.tenant_active = {"tenant-a": True, "tenant-b": True}
        self.members = {"tenant-a": str(uuid4())}
        self.member_active = {"tenant-a": True, "tenant-b": True}

    async def find_by_email(self, email):
        return self.user if email.strip().lower() == self.user.email else None

    async def find_by_id(self, user_id):
        return self.user if user_id == self.user.user_id else None

    async def memberships(self, user_id):
        return [
            dict(tenant_id=t, tenant_name=t, membership_id=m, tenant_role="MEMBER")
            for t, m in self.members.items()
            if self.user.is_active
            and user_id == self.user.user_id
            and self.tenant_active[t]
            and self.member_active[t]
        ]

    async def resolve_context(self, tenant_id, user_id, membership_id=None):
        if (
            tenant_id not in self.members
            or not self.user.is_active
            or user_id != self.user.user_id
            or not self.tenant_active[tenant_id]
            or not self.member_active[tenant_id]
            or membership_id not in (None, self.members[tenant_id])
        ):
            return None
        return dict(
            tenant_id=tenant_id,
            user_id=user_id,
            membership_id=self.members[tenant_id],
            tenant_role="MEMBER",
            email=self.user.email,
            tenant_name=tenant_id,
        )


class RefreshStore:
    def __init__(self):
        self.tokens = {}

    async def set(self, key, value, ex):
        assert ex > 0
        self.tokens[key] = value

    async def get(self, key):
        return self.tokens.get(key)

    async def delete(self, key):
        self.tokens.pop(key, None)


def token(store, **overrides):
    claims = dict(
        sub=store.user.user_id,
        tenant_id="tenant-a",
        membership_id=store.members["tenant-a"],
        type="access",
        exp=datetime.now(UTC) + timedelta(minutes=5),
    )
    claims.update(overrides)
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def bearer(value):
    return {"Authorization": "Bearer " + value}


@pytest.fixture
async def identity_api(monkeypatch):
    store, refresh = IdentityStore(), RefreshStore()
    authorization = AuthorizationService(store)
    monkeypatch.setattr(dependencies, "authorization", authorization)
    monkeypatch.setattr(auth_service, "authorization", authorization)
    monkeypatch.setattr(auth_service, "get_redis", lambda: refresh)
    monkeypatch.setattr(auth, "_service", AuthService(store))
    monkeypatch.setattr(
        "app.infrastructure.postgres.repositories.user_repo.UserRepository", lambda: store
    )
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(auth.me_router)

    @app.exception_handler(AuthorizationError)
    async def forbidden(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code})

    async with AsyncClient(
        transport=ASGITransport(app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        yield SimpleNamespace(client=client, store=store, refresh=refresh)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Tenant-Id": "tenant-a"},
        {"Authorization": "Bearer invalid"},
        {"Authorization": "Basic invalid"},
    ],
)
async def test_authentication_is_required(identity_api, headers):
    response = await identity_api.client.get("/me/context", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": datetime.now(UTC) - timedelta(minutes=1)},
        {"type": "tenant_selection"},
        {"type": "refresh"},
        {"type": []},
        {"sub": "bad-id"},
        {"sub": None},
        {"membership_id": "bad-id"},
        {"membership_id": None},
        {"membership_id": 7},
        {"membership_id": []},
        {"membership_id": {}},
        {"tenant_id": None},
        {"tenant_id": []},
        {"tenant_id": ""},
    ],
)
async def test_invalid_claims_are_401_instead_of_server_errors(identity_api, claims):
    response = await identity_api.client.get(
        "/me/context", headers=bearer(token(identity_api.store, **claims))
    )
    assert response.status_code == 401


@pytest.mark.parametrize("binding", ["membership", "tenant", "user"])
async def test_signed_but_mismatched_bindings_are_rejected(identity_api, binding):
    claims = (
        {"membership_id": str(uuid4())}
        if binding == "membership"
        else ({"tenant_id": "tenant-b"} if binding == "tenant" else {"sub": str(uuid4())})
    )
    response = await identity_api.client.get(
        "/me/context", headers=bearer(token(identity_api.store, **claims))
    )
    assert response.status_code == 403
    assert response.json()["code"] == "TENANT_CONTEXT_INVALID"


@pytest.mark.parametrize("revoked", ["user", "tenant", "membership"])
async def test_current_identity_is_rechecked_on_every_request(identity_api, revoked):
    client, store = identity_api.client, identity_api.store
    headers = bearer(token(store))
    assert (await client.get("/me/context", headers=headers)).status_code == 200
    if revoked == "user":
        store.user.is_active = False
    elif revoked == "tenant":
        store.tenant_active["tenant-a"] = False
    else:
        store.member_active["tenant-a"] = False
    assert (await client.get("/me/context", headers=headers)).status_code == 403


async def test_login_refresh_logout_keep_membership_binding(identity_api):
    client, store = identity_api.client, identity_api.store
    response = await client.post(
        "/auth/login", json={"email": "Person@EXAMPLE.COM", "password": "password123"}
    )
    assert response.status_code == 200
    pair = response.json()
    context = await client.get("/me/context", headers=bearer(pair["access_token"]))
    assert context.json()["membership_id"] == store.members["tenant-a"]
    refreshed = await client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert refreshed.status_code == 200
    assert (
        await client.get("/me/context", headers=bearer(refreshed.json()["access_token"]))
    ).status_code == 200
    logged_out = await client.post(
        "/auth/logout",
        headers=bearer(pair["access_token"]),
        json={"refresh_token": pair["refresh_token"]},
    )
    assert logged_out.status_code == 204
    assert (
        await client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    ).status_code == 401


@pytest.mark.parametrize("revoked", ["user", "tenant", "membership"])
async def test_refresh_cannot_resurrect_revoked_identity(identity_api, revoked):
    store, client = identity_api.store, identity_api.client
    pair = (
        await client.post(
            "/auth/login", json={"email": store.user.email, "password": "password123"}
        )
    ).json()
    if revoked == "user":
        store.user.is_active = False
    elif revoked == "tenant":
        store.tenant_active["tenant-a"] = False
    else:
        store.member_active["tenant-a"] = False
    response = await client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert response.status_code == 403


async def test_multi_tenant_login_selection_and_switch(identity_api):
    client, store = identity_api.client, identity_api.store
    store.members["tenant-b"] = str(uuid4())
    result = (
        await client.post(
            "/auth/login", json={"email": store.user.email, "password": "password123"}
        )
    ).json()
    assert len(result["memberships"]) == 2
    assert "access_token" not in result
    headers = bearer(result["selection_token"])
    assert (await client.get("/me/context", headers=headers)).status_code == 401
    assert (await client.get("/me/tenants", headers=headers)).status_code == 200
    switched = await client.post(
        "/auth/switch-tenant", headers=headers, json={"tenant_id": "tenant-b"}
    )
    assert switched.status_code == 200
    context = (
        await client.get("/me/context", headers=bearer(switched.json()["access_token"]))
    ).json()
    assert context["tenant_id"] == "tenant-b"
    assert context["membership_id"] == store.members["tenant-b"]
    assert (
        await client.post("/auth/switch-tenant", headers=headers, json={"tenant_id": "foreign"})
    ).status_code == 403


@pytest.mark.parametrize(
    "claims",
    [
        {"membership_id": None},
        {"membership_id": 3},
        {"membership_id": str(uuid4())},
        {"tenant_id": "foreign"},
    ],
)
async def test_identity_endpoints_validate_access_token_binding(identity_api, claims):
    response = await identity_api.client.get(
        "/me/tenants", headers=bearer(token(identity_api.store, **claims))
    )
    assert response.status_code in (401, 403)


async def test_wrong_password_cannot_login(identity_api):
    response = await identity_api.client.post(
        "/auth/login", json={"email": identity_api.store.user.email, "password": "wrong-password"}
    )
    assert response.status_code == 401
