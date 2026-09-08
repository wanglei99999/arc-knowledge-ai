import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import admin


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/admin/stats"),
        ("post", "/admin/dev/reset"),
        ("get", "/admin/models"),
        ("get", "/admin/tenants/victim/config"),
        ("get", "/admin/tenants/victim/usage"),
    ],
)
def test_admin_unauthenticated_rejected_before_io(method, path, monkeypatch):
    from app.config.settings import settings

    monkeypatch.setattr(settings, "app_env", "production")

    def offline(*args, **kwargs):
        raise RuntimeError("unauthorized request reached database")

    monkeypatch.setattr(admin, "get_session", offline)
    monkeypatch.setattr(admin._model_config_svc, "list_all", offline)
    monkeypatch.setattr(admin._tenant_config_svc, "get_or_default", offline)
    monkeypatch.setattr(admin._usage_svc, "query_by_tenant", offline)
    app = FastAPI()
    app.include_router(admin.router)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.request(method, path).status_code == 401
