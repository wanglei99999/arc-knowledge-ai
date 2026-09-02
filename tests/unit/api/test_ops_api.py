from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import ops
from app.services.readiness_service import ReadinessReport


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(ops.router)
    return TestClient(app, raise_server_exceptions=False)


def test_health_is_process_only(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_returns_503_for_unhealthy_report(client, monkeypatch):
    monkeypatch.setattr(
        "app.api.routers.ops.get_readiness_report",
        AsyncMock(return_value=ReadinessReport("unhealthy", ())),
    )

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "unhealthy"


def test_ready_returns_200_for_degraded_report(client, monkeypatch):
    monkeypatch.setattr(
        "app.api.routers.ops.get_readiness_report",
        AsyncMock(return_value=ReadinessReport("degraded", ())),
    )

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
