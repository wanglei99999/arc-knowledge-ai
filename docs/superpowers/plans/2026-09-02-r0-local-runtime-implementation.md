# R0 Local Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the complete local knowledge-base application reproducibly start, diagnose, verify, stop, and recover on Windows with Docker Desktop and one PowerShell entry point.

**Architecture:** Build immutable backend and frontend images, run every application and infrastructure component on one Docker Compose network, expose a small PowerShell control plane, and separate liveness, readiness, worker-poller, runtime-smoke, and business-smoke signals. Core storage failures block readiness; model and enabled optional-service failures report degradation and fail smoke verification without making the API process look dead.

**Tech Stack:** Docker Compose, PowerShell 7, Python 3.12, uv, FastAPI, Temporal Python SDK, PostgreSQL, Redis, MinIO, Milvus, Elasticsearch, Vue 3, Vite, Nginx, pytest, Pester, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-01-r0-local-runtime-design.md`

## Global Constraints

- The supported R0 host is Windows with PowerShell 7 and Docker Desktop already installed and running.
- The host must not require Python, Node.js, PostgreSQL, Temporal, Ollama, or project virtual environments to start the application.
- `./incipit.ps1 start` starts the core system; `./incipit.ps1 start -Full` additionally enables `rerank`, `ocr`, and `observe` profiles.
- `stop` preserves named volumes. R0 does not expose a data-reset command.
- Bind published ports to `127.0.0.1` and allow per-port overrides through `.env`.
- `/health` proves that the API process is alive. `/ready` proves core data connectivity and reports model/optional-service degradation.
- API readiness does not claim worker health. Worker readiness is proved by a poller on the configured Temporal task queue.
- L0 smoke proves runtime wiring. L1 smoke proves register/login, workspace creation, upload, ingestion, retrieval, citation-bearing chat, and logical cleanup.
- Never silently download application models during startup. Model locations and endpoints are explicit configuration.
- Pin every runtime image tag; do not use `latest`.
- Keep the two Git repositories independent. Backend/runtime commits belong to `arc-knowledge-ai`; frontend image and CI commits belong to `arc-knowledge-web`.
- Preserve pre-existing user changes. Execution should use sibling isolated worktrees when the worktree skill allows it.
- Every implementation task follows: explain the concept, show the current failure, add a failing test or contract check, implement the smallest change, verify, then commit in the owning repository.

---

## File and Interface Map

### Backend/runtime repository: `arc-knowledge-ai`

- Create `Dockerfile`: one locked Python image reused by `api`, `worker`, `migrate`, and smoke commands.
- Create `.dockerignore`: exclude local environments, caches, secrets, Git data, and generated artifacts.
- Create `app/infrastructure/health.py`: non-mutating health adapters for PostgreSQL, Redis, MinIO, Milvus, Elasticsearch, Temporal, and HTTP model endpoints.
- Create `app/services/readiness_service.py`: aggregate required and optional dependency checks into `healthy`, `degraded`, or `unhealthy`.
- Create `app/api/routers/ops.py`: `/health` and `/ready` response contracts.
- Modify `app/main.py`: register the operations router and remove the inline health endpoint.
- Modify `app/config/settings.py`: add readiness timeout and enabled optional-service configuration.
- Modify `.env.example`: document host-port overrides, internal providers, smoke identity, and profile controls.
- Modify `docker-compose.yml`: pin infrastructure images; add `migrate`, `api`, `worker`, and `web`; define health checks, profiles, named volumes, and loopback ports.
- Create `scripts/runtime/runtime_probe.py`: Temporal worker-poller and machine-readable runtime probes.
- Create `scripts/runtime/bootstrap.py`: idempotent PostgreSQL migration plus MinIO bucket and Elasticsearch index initialization.
- Create `scripts/runtime/smoke.py`: L0 and L1 smoke implementation.
- Create `scripts/runtime/Incipit.Runtime.psm1`: doctor, orchestration, state aggregation, logging, and smoke functions.
- Create `incipit.ps1`: stable user-facing command dispatcher.
- Create `tests/runtime/incipit.Tests.ps1`: Pester tests for command parsing and state transitions.
- Create focused Python unit tests under `tests/unit/infrastructure`, `tests/unit/services`, `tests/unit/api`, and `tests/unit/scripts`.
- Create `.github/workflows/ci.yml`: backend tests, Compose validation, backend image build, and PowerShell contract tests.
- Modify `README.md`: one-command quick start, profiles, state meanings, fault recovery, and data-preservation behavior.

### Frontend repository: `arc-knowledge-web`

- Create `Dockerfile`: locked Node build stage and pinned Nginx runtime stage.
- Create `.dockerignore`: exclude dependency and local output directories.
- Create `nginx.conf`: SPA fallback and unbuffered `/api/` proxy to the backend container.
- Create `tests/container-contract.test.ts`: production image/proxy configuration contract.
- Modify `.github/workflows/ci.yml`: retain existing tests/build and add production image build plus Nginx config validation.

### Stable interfaces

```text
incipit.ps1 doctor
incipit.ps1 start [-Full]
incipit.ps1 stop
incipit.ps1 status [-Json]
incipit.ps1 logs [-Service <name>] [-Follow]
incipit.ps1 smoke [-Level L0|L1]

GET /health -> 200 {"status":"ok","env":"local"}
GET /ready  -> 200 healthy/degraded, 503 unhealthy

ReadinessReport.status = healthy | degraded | unhealthy
ReadinessReport.checks[] = {name, required, status, latency_ms, detail}

runtime_probe.py worker --json -> exit 0 only when a workflow poller exists
smoke.py --level l0|l1       -> exit 0 only when all requested checks pass
```

---

## Task 1: Build a Locked Backend Runtime Image

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `tests/runtime/test_backend_container_contract.py`

**Concept to explain:** A container image is the runtime artifact; Compose should choose the command, not rebuild separate environments for API, worker, migration, and smoke.

**Interface:** The image starts the API by default and accepts command overrides such as `python scripts/migrate.py` and `python scripts/start_worker.py`.

- [ ] **Step 1: Create the failing image-contract test**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_backend_image_is_locked_and_non_root():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "python:3.12.11-slim-bookworm" in dockerfile
    assert "ghcr.io/astral-sh/uv:0.12.5" in dockerfile
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "USER app" in dockerfile
    assert '["uvicorn", "app.main:app"' in dockerfile


def test_backend_context_excludes_secrets_and_host_environments():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for required in (".env", ".venv", ".git", "__pycache__", ".pytest_cache"):
        assert required in ignored
```

- [ ] **Step 2: Run the focused test and confirm it fails because the files do not exist**

```powershell
uv sync --extra dev --frozen
uv run pytest tests/runtime/test_backend_container_contract.py -q
```

Expected: `FileNotFoundError` for `Dockerfile`.

- [ ] **Step 3: Add the production Dockerfile**

```dockerfile
FROM ghcr.io/astral-sh/uv:0.12.5 AS uv
FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libmagic1 poppler-utils \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app

COPY --from=uv /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY scripts ./scripts
COPY tests/fixtures ./tests/fixtures
RUN uv sync --frozen --no-dev \
    && chown -R app:app /app

USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 4: Add the Docker context exclusions**

```gitignore
.git
.github
.env
.env.*
!.env.example
.venv
__pycache__
*.py[cod]
.pytest_cache
.ruff_cache
.mypy_cache
htmlcov
.coverage
data
logs
docs
tests/*
!tests/fixtures/
!tests/fixtures/**
```

- [ ] **Step 5: Verify the contract and image runtime**

```powershell
uv run pytest tests/runtime/test_backend_container_contract.py -q
docker build --tag incipit-api:r0 .
docker run --rm incipit-api:r0 python -c "import app.main; print('backend-image-ok')"
```

Expected: the test passes and the container prints `backend-image-ok`.

- [ ] **Step 6: Commit in `arc-knowledge-ai`**

```powershell
git add Dockerfile .dockerignore tests/runtime/test_backend_container_contract.py
git commit -m "build: add locked backend runtime image"
```

---

## Task 2: Build the Frontend Production Image and Same-Origin API Proxy

**Files:**
- Create: `arc-knowledge-web/Dockerfile`
- Create: `arc-knowledge-web/.dockerignore`
- Create: `arc-knowledge-web/nginx.conf`
- Create: `arc-knowledge-web/tests/container-contract.test.ts`

**Concept to explain:** The browser cannot resolve Docker service names. Nginx gives the browser one origin and proxies `/api/` inside the Compose network, which also avoids CORS and preserves SSE streaming.

**Interface:** The image serves the SPA on port 80; frontend code is built with `VITE_API_BASE_URL=/api`; Nginx strips `/api/` before forwarding to `http://api:8000/`.

- [ ] **Step 1: Add the failing Vitest contract**

```typescript
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

describe('production container contract', () => {
  it('builds the frontend against the same-origin API path', () => {
    const dockerfile = readFileSync(new URL('../Dockerfile', import.meta.url), 'utf8')
    expect(dockerfile).toContain('node:20.19.4-alpine')
    expect(dockerfile).toContain('nginx:1.27.5-alpine')
    expect(dockerfile).toContain('ARG VITE_API_BASE_URL=/api')
    expect(dockerfile).toContain('npm ci')
  })

  it('disables buffering for streamed chat responses', () => {
    const nginx = readFileSync(new URL('../nginx.conf', import.meta.url), 'utf8')
    expect(nginx).toContain('location /api/')
    expect(nginx).toContain('proxy_pass http://api:8000/')
    expect(nginx).toContain('proxy_buffering off')
    expect(nginx).toContain('try_files $uri $uri/ /index.html')
  })
})
```

- [ ] **Step 2: Run the contract and confirm the missing-file failure**

```powershell
npm ci
npm test -- tests/container-contract.test.ts
```

Expected: the test fails while reading `Dockerfile`.

- [ ] **Step 3: Add the two-stage image**

```dockerfile
FROM node:20.19.4-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:1.27.5-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
```

- [ ] **Step 4: Add the Nginx configuration**

```nginx
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    location /api/ {
        proxy_pass http://api:8000/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

- [ ] **Step 5: Add `.dockerignore`**

```gitignore
.git
.github
.env
.env.*
node_modules
dist
coverage
playwright-report
test-results
```

- [ ] **Step 6: Verify tests, config, and image**

```powershell
npm test -- tests/container-contract.test.ts
npm run build
docker build --tag incipit-web:r0 .
docker run --rm incipit-web:r0 nginx -t
```

Expected: Vitest and build pass; Nginx reports that configuration syntax is valid.

- [ ] **Step 7: Commit in `arc-knowledge-web`**

```powershell
git add Dockerfile .dockerignore nginx.conf tests/container-contract.test.ts
git commit -m "build: add frontend production image"
```

---

## Task 3: Implement Dependency Health Adapters and Readiness Aggregation

**Files:**
- Create: `app/infrastructure/health.py`
- Create: `app/services/readiness_service.py`
- Create: `app/api/routers/ops.py`
- Modify: `app/config/settings.py`
- Modify: `app/main.py`
- Create: `tests/unit/infrastructure/test_health.py`
- Create: `tests/unit/services/test_readiness_service.py`
- Create: `tests/unit/api/test_ops_api.py`

**Concept to explain:** Liveness answers “is the process running?” while readiness answers “can this process safely serve traffic?” A model outage degrades answer quality, but a PostgreSQL outage makes core requests unsafe.

**Interfaces:**

```python
Check = Callable[[], Awaitable[None]]

@dataclass(frozen=True)
class DependencyCheck:
    name: str
    required: bool
    status: Literal["ok", "failed", "skipped"]
    latency_ms: int
    detail: str

@dataclass(frozen=True)
class ReadinessReport:
    status: Literal["healthy", "degraded", "unhealthy"]
    checks: tuple[DependencyCheck, ...]

class ReadinessService:
    async def check(self) -> ReadinessReport
```

- [ ] **Step 1: Add failing aggregation tests**

```python
import pytest

from app.services.readiness_service import ReadinessService


async def succeeds() -> None:
    return None


async def fails() -> None:
    raise ConnectionError("offline")


@pytest.mark.asyncio
async def test_required_failure_is_unhealthy():
    report = await ReadinessService(
        required={"postgres": fails}, optional={"llm": succeeds}
    ).check()
    assert report.status == "unhealthy"
    assert next(item for item in report.checks if item.name == "postgres").detail == "offline"


@pytest.mark.asyncio
async def test_optional_failure_is_degraded():
    report = await ReadinessService(
        required={"postgres": succeeds}, optional={"llm": fails}
    ).check()
    assert report.status == "degraded"


@pytest.mark.asyncio
async def test_all_success_is_healthy():
    report = await ReadinessService(
        required={"postgres": succeeds}, optional={"llm": succeeds}
    ).check()
    assert report.status == "healthy"
```

- [ ] **Step 2: Run and confirm the import failure**

```powershell
uv run pytest tests/unit/services/test_readiness_service.py -q
```

Expected: import of `app.services.readiness_service` fails.

- [ ] **Step 3: Implement bounded concurrent aggregation**

```python
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Awaitable, Callable, Literal, Mapping

Check = Callable[[], Awaitable[None]]
CheckStatus = Literal["ok", "failed", "skipped"]
ReadyStatus = Literal["healthy", "degraded", "unhealthy"]


@dataclass(frozen=True)
class DependencyCheck:
    name: str
    required: bool
    status: CheckStatus
    latency_ms: int
    detail: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ReadinessReport:
    status: ReadyStatus
    checks: tuple[DependencyCheck, ...]

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status, "checks": [item.to_dict() for item in self.checks]}


class ReadinessService:
    def __init__(
        self,
        required: Mapping[str, Check],
        optional: Mapping[str, Check],
        timeout_seconds: float = 3.0,
    ) -> None:
        self._required = required
        self._optional = optional
        self._timeout_seconds = timeout_seconds

    async def _run(self, name: str, required: bool, check: Check) -> DependencyCheck:
        started = perf_counter()
        try:
            await asyncio.wait_for(check(), timeout=self._timeout_seconds)
            return DependencyCheck(name, required, "ok", int((perf_counter() - started) * 1000), "ok")
        except Exception as exc:
            return DependencyCheck(name, required, "failed", int((perf_counter() - started) * 1000), str(exc))

    async def check(self) -> ReadinessReport:
        checks = await asyncio.gather(*(
            [self._run(name, True, check) for name, check in self._required.items()]
            + [self._run(name, False, check) for name, check in self._optional.items()]
        ))
        if any(item.required and item.status == "failed" for item in checks):
            status: ReadyStatus = "unhealthy"
        elif any(item.status == "failed" for item in checks):
            status = "degraded"
        else:
            status = "healthy"
        return ReadinessReport(status=status, checks=tuple(checks))
```

- [ ] **Step 4: Add mock-backed health-adapter tests**

Cover these exact behaviors in `tests/unit/infrastructure/test_health.py` using concrete async stubs:

```python
class ResponseStub:
    def __init__(self, status_code: int):
        self.status_code = status_code


class HttpClientStub:
    def __init__(self, status_code: int):
        self.status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers):
        return ResponseStub(self.status_code)


@pytest.mark.asyncio
async def test_http_probe_accepts_401_as_reachable(monkeypatch):
    monkeypatch.setattr("app.infrastructure.health.httpx.AsyncClient", lambda **_: HttpClientStub(401))
    await check_http_endpoint("http://model.test/v1", "secret")


@pytest.mark.asyncio
async def test_http_probe_rejects_500(monkeypatch):
    monkeypatch.setattr("app.infrastructure.health.httpx.AsyncClient", lambda **_: HttpClientStub(500))
    with pytest.raises(ConnectionError, match="HTTP 500"):
        await check_http_endpoint("http://model.test/v1")
```

Add equally concrete PostgreSQL and Redis stubs that assert `SELECT 1` and `PING`; keep all external clients mocked so this unit test never needs Docker.

```python
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_postgres_probe_executes_select_one(monkeypatch):
    session = type("SessionStub", (), {"execute": AsyncMock()})()

    @asynccontextmanager
    async def session_scope():
        yield session

    monkeypatch.setattr("app.infrastructure.health.get_session", session_scope)
    await check_postgres()
    assert str(session.execute.await_args.args[0]) == "SELECT 1"


@pytest.mark.asyncio
async def test_redis_probe_requires_ping_true(monkeypatch):
    redis = type("RedisStub", (), {"ping": AsyncMock(return_value=False)})()
    monkeypatch.setattr("app.infrastructure.health.get_redis", lambda: redis)
    with pytest.raises(ConnectionError, match="PING"):
        await check_redis()
```

The HTTP rule is intentional: authentication failure still proves that an endpoint is reachable, while a 5xx response indicates an unhealthy provider.

- [ ] **Step 5: Implement non-mutating adapters in `app/infrastructure/health.py`**

```python
from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
from sqlalchemy import text
from temporalio.client import Client

from app.config.settings import settings
from app.infrastructure.elasticsearch.client import _get_client as get_es_client
from app.infrastructure.milvus.client import _get_client as get_milvus_client
from app.infrastructure.minio.client import _make_client as get_minio_client
from app.infrastructure.postgres.client import get_session
from app.infrastructure.redis.client import get_redis


async def check_postgres() -> None:
    async with get_session() as session:
        await session.execute(text("SELECT 1"))


async def check_redis() -> None:
    if not await get_redis().ping():
        raise ConnectionError("Redis PING returned false")


async def check_minio() -> None:
    client = get_minio_client()
    await asyncio.to_thread(client.head_bucket, Bucket=settings.minio_bucket)


async def check_milvus() -> None:
    client = get_milvus_client()
    collections = await asyncio.to_thread(client.list_collections)
    if "arc_chunk_embeddings" in collections:
        await asyncio.to_thread(client.get_collection_stats, "arc_chunk_embeddings")


async def check_elasticsearch() -> None:
    client = get_es_client()
    if not await client.ping():
        raise ConnectionError("Elasticsearch ping returned false")
    if not await client.indices.exists(index="arc_chunks"):
        raise ConnectionError("Elasticsearch index arc_chunks is missing")
    await client.indices.stats(index="arc_chunks")


async def check_temporal() -> None:
    client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)
    if not await client.service_client.check_health(timeout=timedelta(seconds=2)):
        raise ConnectionError("Temporal health check returned false")


async def check_http_endpoint(url: str, api_key: str = "") -> None:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=settings.runtime_probe_timeout_seconds) as client:
        response = await client.get(url.rstrip("/"), headers=headers)
    if response.status_code >= 500:
        raise ConnectionError(f"endpoint returned HTTP {response.status_code}")
```

- [ ] **Step 6: Add settings and construct the default check sets**

Add to `Settings`:

```python
runtime_probe_timeout_seconds: float = 3.0
runtime_optional_services: str = ""
```

Add a `build_readiness_service()` factory to `health.py`. Required checks are PostgreSQL, Redis, MinIO, Milvus, Elasticsearch, and Temporal. Optional checks always include configured LLM and embedding endpoints. Token `rerank` enables the Infinity check, token `ocr` enables both PaddleOCR and MinerU checks, and token `observe` enables the Phoenix check.

Use this exact parser so profile names do not depend on whitespace or case:

```python
def enabled_optional_services() -> set[str]:
    return {
        item.strip().lower()
        for item in settings.runtime_optional_services.split(",")
        if item.strip()
    }
```

- [ ] **Step 7: Add API contract tests before registering the router**

```python
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
    assert client.get("/ready").status_code == 200
```

- [ ] **Step 8: Implement and register `ops.py`**

```python
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config.settings import settings
from app.infrastructure.health import build_readiness_service

router = APIRouter(tags=["operations"])


async def get_readiness_report():
    return await build_readiness_service().check()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}


@router.get("/ready")
async def ready() -> JSONResponse:
    report = await get_readiness_report()
    return JSONResponse(report.to_dict(), status_code=503 if report.status == "unhealthy" else 200)
```

Remove the inline `/health` function from `app/main.py`, import `ops`, and call `app.include_router(ops.router)`.

- [ ] **Step 9: Run focused and regression tests**

```powershell
uv run pytest tests/unit/infrastructure/test_health.py tests/unit/services/test_readiness_service.py tests/unit/api/test_ops_api.py -q
uv run pytest tests/unit -q
uv run ruff check app tests
```

- [ ] **Step 10: Commit in `arc-knowledge-ai`**

```powershell
git add app/config/settings.py app/infrastructure/health.py app/services/readiness_service.py app/api/routers/ops.py app/main.py tests/unit
git commit -m "feat: add runtime readiness reporting"
```

---

## Task 4: Define the Reproducible Compose Runtime

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Create: `scripts/runtime/bootstrap.py`
- Create: `tests/unit/scripts/test_runtime_bootstrap.py`
- Create: `tests/runtime/test_compose_contract.py`

**Concept to explain:** Compose is both a dependency graph and a network contract. Container-to-container traffic uses service names and internal ports; published host ports exist only for the developer and bind to loopback.

**Interfaces:**

- Core services: `postgres`, `minio`, `etcd`, `milvus`, `elasticsearch`, `redis`, `temporal`, `temporal-ui`, `migrate`, `api`, `worker`, `web`.
- Profile `rerank`: `infinity`.
- Profile `ocr`: `paddleocr`, `mineru`.
- Profile `observe`: `prometheus`, `grafana`, `phoenix`.
- Named volumes survive `docker compose down` because the management command never passes `--volumes`.

- [ ] **Step 1: Add a failing Compose contract test**

```python
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def load_compose():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_application_services_share_one_backend_image():
    services = load_compose()["services"]
    assert services["api"]["image"] == "incipit-api:r0"
    assert services["worker"]["image"] == "incipit-api:r0"
    assert services["migrate"]["image"] == "incipit-api:r0"


def test_every_published_port_binds_loopback():
    for service in load_compose()["services"].values():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:")


def test_no_runtime_image_uses_latest():
    for service in load_compose()["services"].values():
        image = service.get("image", "")
        assert not image.endswith(":latest")


def test_optional_services_have_expected_profiles():
    services = load_compose()["services"]
    assert services["infinity"]["profiles"] == ["rerank"]
    assert services["paddleocr"]["profiles"] == ["ocr"]
    assert services["mineru"]["profiles"] == ["ocr"]
    for name in ("prometheus", "grafana", "phoenix"):
        assert services[name]["profiles"] == ["observe"]
```

If PyYAML is not already available to the dev dependency group, add `pyyaml>=6.0.2` to the dev group and refresh `uv.lock` as part of this step.

- [ ] **Step 2: Run and observe failures against the current infrastructure-only Compose file**

```powershell
uv run pytest tests/runtime/test_compose_contract.py -q
```

Expected: missing `api`, `worker`, `migrate`, and `web`; existing `latest` tags also fail.

- [ ] **Step 3: Replace floating tags with these exact R0 pins**

```text
postgres:16.3-alpine
minio/minio:RELEASE.2024-07-16T23-46-41Z
quay.io/coreos/etcd:v3.5.14
milvusdb/milvus:v2.4.9
docker.elastic.co/elasticsearch/elasticsearch:8.13.4
redis/redis-stack-server:7.4.0-v5
temporalio/auto-setup:1.24.2
temporalio/ui:2.26.2
michaelf34/infinity:0.0.77-cpu
prom/prometheus:v2.52.0
grafana/grafana:10.4.2
arizephoenix/phoenix:version-18.0.0
```

Retain the existing exact OCR image tags. Before editing YAML, run `docker manifest inspect <image:tag>` for every newly pinned tag; if a named tag is unavailable, choose the nearest compatible immutable tag from that image's official registry, record the substitution in the plan execution notes, and rerun the manifest check. Do not replace a failed pin with `latest`.

- [ ] **Step 4: Add a common application environment mapping**

```yaml
x-app-environment: &app-environment
  APP_ENV: local
  POSTGRES_URL: postgresql+asyncpg://arc:arc@postgres:5432/arc_knowledge
  REDIS_URL: redis://redis:6379/0
  MINIO_ENDPOINT: minio:9000
  MINIO_ACCESS_KEY: ${MINIO_ACCESS_KEY:-minioadmin}
  MINIO_SECRET_KEY: ${MINIO_SECRET_KEY:-minioadmin}
  MINIO_SECURE: "false"
  JWT_SECRET_KEY: ${JWT_SECRET_KEY:-change-this-for-any-shared-environment}
  MILVUS_HOST: milvus
  MILVUS_PORT: "19530"
  ES_URL: http://elasticsearch:9200
  TEMPORAL_HOST: temporal:7233
  TEMPORAL_NAMESPACE: default
  TEMPORAL_TASK_QUEUE: arc-ingestion
  INFINITY_BASE_URL: http://infinity:7997
  OCR_SERVICE_URL: http://paddleocr:7998
  MINERU_SERVICE_URL: http://mineru:7999
  OTEL_EXPORTER_OTLP_ENDPOINT: http://phoenix:4317
  RUNTIME_OPTIONAL_SERVICES: ${INCIPIT_OPTIONAL_SERVICES:-}
  RUNTIME_PROBE_TIMEOUT_SECONDS: ${RUNTIME_PROBE_TIMEOUT_SECONDS:-3}
  LLM_PROVIDER: ${LLM_PROVIDER:-ollama}
  LLM_BASE_URL: ${LLM_BASE_URL:-http://host.docker.internal:11434/v1}
  LLM_MODEL: ${LLM_MODEL:-qwen2.5:7b}
  LLM_API_KEY: ${LLM_API_KEY:-ollama}
  EMBEDDING_PROVIDER: ${EMBEDDING_PROVIDER:-ollama}
  EMBEDDING_BASE_URL: ${EMBEDDING_BASE_URL:-http://host.docker.internal:11434/v1}
  EMBEDDING_MODEL: ${EMBEDDING_MODEL:-nomic-embed-text}
  EMBEDDING_API_KEY: ${EMBEDDING_API_KEY:-ollama}
  SMOKE_TENANT_ID: ${SMOKE_TENANT_ID:-r0-smoke}
  SMOKE_EMAIL: ${SMOKE_EMAIL:-r0-smoke@example.com}
  SMOKE_PASSWORD: ${SMOKE_PASSWORD:-R0-local-smoke-only-2026}
```

Use `host.docker.internal` only for explicitly host-run model servers. All Compose-managed dependencies use service DNS names.

- [ ] **Step 5: Add application services**

```yaml
services:
  migrate:
    image: incipit-api:r0
    build:
      context: .
    command: ["python", "scripts/runtime/bootstrap.py"]
    environment: *app-environment
    restart: "no"
    depends_on:
      postgres:
        condition: service_healthy

  api:
    image: incipit-api:r0
    build:
      context: .
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
    environment: *app-environment
    ports:
      - "127.0.0.1:${API_HOST_PORT:-8000}:8000"
    restart: unless-stopped
    extra_hosts:
      - "host.docker.internal:host-gateway"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=4)"]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 20s

  worker:
    image: incipit-api:r0
    command: ["python", "scripts/start_worker.py"]
    environment: *app-environment
    restart: unless-stopped
    extra_hosts:
      - "host.docker.internal:host-gateway"

  web:
    image: incipit-web:r0
    build:
      context: ../arc-knowledge-web
      args:
        VITE_API_BASE_URL: /api
    ports:
      - "127.0.0.1:${WEB_HOST_PORT:-3300}:80"
    restart: unless-stopped
    depends_on:
      api:
        condition: service_healthy
```

The final YAML must merge these services into the existing file instead of creating a second `services:` key.

- [ ] **Step 6: Make first-run storage initialization idempotent**

First add `tests/unit/scripts/test_runtime_bootstrap.py` with async mocks proving that database migration runs before storage initialization and that the same bootstrap function can be called twice:

```python
from unittest.mock import AsyncMock, Mock

import pytest

from scripts.runtime import bootstrap as target


@pytest.mark.asyncio
async def test_bootstrap_is_ordered_and_repeatable(monkeypatch):
    events: list[str] = []
    migrate = AsyncMock(side_effect=lambda: events.append("database"))
    ensure_bucket = Mock(side_effect=lambda *_: events.append("minio"))
    ensure_index = Mock(side_effect=lambda *_: events.append("elasticsearch"))
    monkeypatch.setattr(target, "migrate", migrate)
    monkeypatch.setattr(target, "get_minio_client", lambda: object())
    monkeypatch.setattr(target, "_ensure_bucket", ensure_bucket)
    monkeypatch.setattr(target, "get_es_client", lambda: object())
    monkeypatch.setattr(target, "_ensure_index", ensure_index)

    await target.bootstrap()
    await target.bootstrap()

    assert events == [
        "database", "minio", "elasticsearch",
        "database", "minio", "elasticsearch",
    ]
```

Then implement:

```python
from __future__ import annotations

import asyncio

from app.config.settings import settings
from app.infrastructure.elasticsearch.client import _ensure_index, _get_client as get_es_client
from app.infrastructure.minio.client import _ensure_bucket, _make_client as get_minio_client
from scripts.migrate import migrate


async def bootstrap() -> None:
    await migrate()
    minio = get_minio_client()
    await asyncio.to_thread(_ensure_bucket, minio, settings.minio_bucket)
    elasticsearch = get_es_client()
    await asyncio.to_thread(_ensure_index, elasticsearch)
    print("Runtime storage initialization completed successfully.")


if __name__ == "__main__":
    asyncio.run(bootstrap())
```

Milvus collection creation remains lazy because its vector dimension must equal the configured embedding model's real output dimension. Readiness still verifies Milvus connectivity, and L1 proves collection creation with the actual model.

- [ ] **Step 7: Apply profiles and loopback port overrides**

Add the exact profiles listed in the interface section. Convert every existing port mapping to this form:

```yaml
ports:
  - "127.0.0.1:${POSTGRES_HOST_PORT:-5432}:5432"
```

Use these variable names: `POSTGRES_HOST_PORT`, `MINIO_API_HOST_PORT`, `MINIO_CONSOLE_HOST_PORT`, `MILVUS_HOST_PORT`, `ELASTICSEARCH_HOST_PORT`, `REDIS_HOST_PORT`, `TEMPORAL_HOST_PORT`, `TEMPORAL_UI_HOST_PORT`, `INFINITY_HOST_PORT`, `PADDLEOCR_HOST_PORT`, `MINERU_HOST_PORT`, `PROMETHEUS_HOST_PORT`, `GRAFANA_HOST_PORT`, `PHOENIX_HOST_PORT`, `PHOENIX_OTLP_HOST_PORT`, `API_HOST_PORT`, and `WEB_HOST_PORT`. Do not publish Milvus port 9091 to the host; Prometheus can scrape it over the Compose network.

Set `HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-1}` and `TRANSFORMERS_OFFLINE=${TRANSFORMERS_OFFLINE:-1}` on Infinity, PaddleOCR, and MinerU. Mount Infinity's local model directory read-only and keep the OCR/Hugging Face caches in named volumes. A missing model must make that optional container unhealthy with a useful log; startup must not silently download gigabytes.

- [ ] **Step 8: Replace `.env.example` with the public host contract**

The file must contain real local defaults and comments but no secrets copied from `.env`:

```dotenv
COMPOSE_PROJECT_NAME=incipit

POSTGRES_HOST_PORT=5432
MINIO_API_HOST_PORT=9000
MINIO_CONSOLE_HOST_PORT=9001
MILVUS_HOST_PORT=19530
ELASTICSEARCH_HOST_PORT=9200
REDIS_HOST_PORT=6379
TEMPORAL_HOST_PORT=7233
TEMPORAL_UI_HOST_PORT=8233
API_HOST_PORT=8000
WEB_HOST_PORT=3300
INFINITY_HOST_PORT=7997
PADDLEOCR_HOST_PORT=7998
MINERU_HOST_PORT=7999
PROMETHEUS_HOST_PORT=9090
GRAFANA_HOST_PORT=3301
PHOENIX_HOST_PORT=6006
PHOENIX_OTLP_HOST_PORT=4317

MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
JWT_SECRET_KEY=change-this-for-any-shared-environment

LLM_PROVIDER=ollama
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_MODEL=qwen2.5:7b
LLM_API_KEY=ollama
EMBEDDING_PROVIDER=ollama
EMBEDDING_BASE_URL=http://host.docker.internal:11434/v1
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_API_KEY=ollama

SMOKE_TENANT_ID=r0-smoke
SMOKE_EMAIL=r0-smoke@example.com
SMOKE_PASSWORD=R0-local-smoke-only-2026
```

Also document all optional profile port variables below these core settings.

- [ ] **Step 9: Validate rendered Compose for core and full modes**

```powershell
uv run pytest tests/runtime/test_compose_contract.py -q
uv run pytest tests/unit/scripts/test_runtime_bootstrap.py -q
docker compose config --quiet
docker compose --profile rerank --profile ocr --profile observe config --quiet
docker compose config --images
```

Expected: both configurations validate; the image list has no `latest`; all published ports start with `127.0.0.1`.

- [ ] **Step 10: Commit in `arc-knowledge-ai`**

```powershell
git add docker-compose.yml .env.example scripts/runtime/bootstrap.py tests/unit/scripts/test_runtime_bootstrap.py pyproject.toml uv.lock tests/runtime/test_compose_contract.py
git commit -m "build: define reproducible compose runtime"
```

---

## Task 5: Add Temporal Worker Probe and Diagnostic State Model

**Files:**
- Create: `scripts/__init__.py`
- Create: `scripts/runtime/__init__.py`
- Create: `scripts/runtime/runtime_probe.py`
- Create: `scripts/runtime/Incipit.Runtime.psm1`
- Create: `tests/unit/scripts/test_runtime_probe.py`
- Create: `tests/runtime/incipit.Tests.ps1`

**Concept to explain:** A running worker process is not enough. Temporal is ready only when it reports a poller on the exact task queue used by workflows.

**Interfaces:**

```text
python scripts/runtime/runtime_probe.py worker [--json]
python scripts/runtime/runtime_probe.py schema [--json]
exit 0: at least one workflow poller on settings.temporal_task_queue
exit 0: every migration-owned PostgreSQL table exists for the schema probe
exit 1: Temporal reachable but no poller, or the schema is incomplete
exit 2: probe execution failed
```

PowerShell diagnostic records use `{Name, Required, State, Detail}` where `State` is `PASS`, `WARN`, or `FAIL`.

- [ ] **Step 1: Add failing unit tests for poller evaluation**

```python
from scripts.runtime.runtime_probe import missing_schema_tables, worker_state


def test_worker_state_passes_with_a_workflow_poller():
    assert worker_state([{"identity": "worker@container"}]) == (True, "1 workflow poller")


def test_worker_state_fails_without_pollers():
    assert worker_state([]) == (False, "no workflow poller")


def test_missing_schema_tables_reports_only_absent_tables():
    assert missing_schema_tables({"users", "spaces"}, {"users", "spaces", "documents"}) == ["documents"]
```

- [ ] **Step 2: Run and confirm the missing-module failure**

```powershell
uv run pytest tests/unit/scripts/test_runtime_probe.py -q
```

- [ ] **Step 3: Implement the worker probe**

```python
from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import text
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client

from app.config.settings import settings
from app.infrastructure.postgres.client import get_session


REQUIRED_TABLES = {
    "users", "spaces", "documents", "document_chunks", "sessions", "messages",
    "message_attachments", "message_citations", "memories", "tenant_configs",
    "model_configs", "usage_records",
}


def worker_state(pollers: list[object]) -> tuple[bool, str]:
    count = len(pollers)
    return (True, f"{count} workflow poller") if count else (False, "no workflow poller")


def missing_schema_tables(existing: set[str], required: set[str] = REQUIRED_TABLES) -> list[str]:
    return sorted(required - existing)


async def probe_schema() -> dict[str, object]:
    async with get_session() as session:
        result = await session.execute(text(
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public'"
        ))
    missing = missing_schema_tables(set(result.scalars()))
    return {
        "name": "schema",
        "ok": not missing,
        "detail": "all migration tables present" if not missing else f"missing tables: {', '.join(missing)}",
    }


async def probe_worker() -> dict[str, object]:
    client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)
    response = await client.workflow_service.describe_task_queue(
        DescribeTaskQueueRequest(
            namespace=settings.temporal_namespace,
            task_queue=TaskQueue(name=settings.temporal_task_queue),
            task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
        )
    )
    ok, detail = worker_state(list(response.pollers))
    return {
        "name": "worker",
        "ok": ok,
        "detail": detail,
        "task_queue": settings.temporal_task_queue,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("probe", choices=["worker", "schema"])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = await (probe_worker() if args.probe == "worker" else probe_schema())
    except Exception as exc:
        result = {"name": args.probe, "ok": False, "detail": str(exc)}
        print(json.dumps(result) if args.json else result["detail"])
        return 2
    print(json.dumps(result) if args.json else result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 4: Add failing Pester tests for overall state**

```powershell
BeforeAll {
    Import-Module "$PSScriptRoot/../../scripts/runtime/Incipit.Runtime.psm1" -Force
}

Describe 'Get-IncipitOverallState' {
    It 'returns STOPPED when no managed container is running' {
        Get-IncipitOverallState @() | Should -Be 'STOPPED'
    }

    It 'returns DEGRADED for an optional warning' {
        $checks = @(
            [pscustomobject]@{ Required = $true; State = 'PASS' },
            [pscustomobject]@{ Required = $false; State = 'WARN' }
        )
        Get-IncipitOverallState $checks | Should -Be 'DEGRADED'
    }

    It 'returns UNHEALTHY for a required failure' {
        $checks = @([pscustomobject]@{ Required = $true; State = 'FAIL' })
        Get-IncipitOverallState $checks | Should -Be 'UNHEALTHY'
    }

    It 'returns HEALTHY when every check passes' {
        $checks = @([pscustomobject]@{ Required = $true; State = 'PASS' })
        Get-IncipitOverallState $checks | Should -Be 'HEALTHY'
    }
}
```

- [ ] **Step 5: Implement the pure state reducer first**

```powershell
function Get-IncipitOverallState {
    [CmdletBinding()]
    param([object[]]$Checks)

    if ($Checks.Count -eq 0) { return 'STOPPED' }
    if ($Checks | Where-Object { $_.Required -and $_.State -eq 'FAIL' }) { return 'UNHEALTHY' }
    if ($Checks | Where-Object { $_.State -in @('WARN', 'FAIL') }) { return 'DEGRADED' }
    return 'HEALTHY'
}
```

- [ ] **Step 6: Add doctor checks with injectable command wrappers**

Implement these exported functions in `Incipit.Runtime.psm1`:

```powershell
function Invoke-IncipitDocker { param([string[]]$Arguments) & docker @Arguments }
function Test-IncipitCommand { param([string]$Name) return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue) }
function Test-IncipitPort { param([int]$Port) return $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) }
function Invoke-IncipitDoctor { param([switch]$Json, [switch]$Full) }
function Get-IncipitOverallState { param([object[]]$Checks) }
```

`Invoke-IncipitDoctor` must report:

- PowerShell major version 7 or newer: required.
- `docker` command: required.
- `docker info`: required and proves Docker Desktop is running.
- `docker compose version`: required.
- Compose file and sibling frontend directory: required.
- Available Docker memory below 8 GiB: failure; below 12 GiB: warning.
- Available Docker disk below 20 GiB: failure; below 40 GiB: warning.
- Core host-port conflicts: failure only when the owning container is not part of this Compose project.
- `.env` missing: warning with the exact copy command `Copy-Item .env.example .env`.
- Missing required `.env` fields or invalid `docker compose config`: failure.
- Configured model endpoints unreachable: warning, because startup may proceed to a degraded state.
- When a model URL contains `host.docker.internal`, doctor probes the same port on `127.0.0.1` from Windows and reports the original configured URL in its detail.
- With `-Full`, missing local Infinity model files or empty required OCR/Hugging Face caches are warnings that name the expected cache path; doctor never initiates a download.

The function returns objects as well as rendering a table. With `-Json`, emit `ConvertTo-Json -Depth 5` only, so callers can parse it.

- [ ] **Step 7: Test diagnostic behavior with mocks**

Add Pester cases that mock `Invoke-IncipitDocker`, `Test-IncipitCommand`, and `Test-IncipitPort` for Docker stopped, low memory, clean machine, and occupied API port. No test may stop Docker Desktop or bind a real user port.

Run:

```powershell
Invoke-Pester tests/runtime/incipit.Tests.ps1 -Output Detailed
uv run pytest tests/unit/scripts/test_runtime_probe.py -q
```

- [ ] **Step 8: Verify the probe against a running worker when available**

```powershell
docker compose exec -T api python scripts/runtime/runtime_probe.py worker --json
```

Before the worker exists, expected exit is 1 with `no workflow poller`; after Task 6 startup, repeat and expect exit 0.

- [ ] **Step 9: Commit in `arc-knowledge-ai`**

```powershell
git add scripts/__init__.py scripts/runtime tests/unit/scripts/test_runtime_probe.py tests/runtime/incipit.Tests.ps1
git commit -m "feat: add local runtime diagnostics"
```

---

## Task 6: Implement Start, Stop, Status, and Logs Commands

**Files:**
- Create: `incipit.ps1`
- Modify: `scripts/runtime/Incipit.Runtime.psm1`
- Modify: `tests/runtime/incipit.Tests.ps1`

**Concept to explain:** Startup is a state machine, not one large `docker compose up`. Infrastructure must become healthy before schema migration, and migration must finish before API/worker/web startup.

**Startup sequence:**

```text
doctor
  -> build application images
  -> start core infrastructure
  -> wait for infrastructure health
  -> run one-shot migration
  -> start API and wait for /ready
  -> start worker and wait for Temporal poller
  -> start web and wait for HTTP 200
  -> print HEALTHY or DEGRADED summary
```

- [ ] **Step 1: Add failing Pester tests for command ordering**

Mock the internal functions and assert this exact sequence:

```powershell
Describe 'Start-Incipit' {
    It 'migrates before starting application processes' {
        Mock Invoke-IncipitDoctor { @([pscustomobject]@{ Required=$true; State='PASS' }) }
        Mock Build-IncipitImages {}
        Mock Start-IncipitInfrastructure {}
        Mock Wait-IncipitInfrastructure {}
        Mock Invoke-IncipitMigration {}
        Mock Start-IncipitApplications {}
        Mock Wait-IncipitApplications {}

        Start-Incipit

        Should -Invoke Build-IncipitImages -Times 1
        Should -Invoke Invoke-IncipitMigration -Times 1
        Should -Invoke Start-IncipitApplications -Times 1
    }

    It 'maps Full to all optional profiles' {
        Mock Invoke-IncipitDoctor { @([pscustomobject]@{ Required=$true; State='PASS' }) }
        Mock Invoke-IncipitDocker {}
        Start-Incipit -Full
        Should -Invoke Invoke-IncipitDocker -ParameterFilter {
            ($Arguments -join ' ') -match '--profile rerank' -and
            ($Arguments -join ' ') -match '--profile ocr' -and
            ($Arguments -join ' ') -match '--profile observe'
        }
    }
}
```

Also test that `Stop-Incipit` invokes `docker compose down --remove-orphans` and never includes `--volumes` or `-v`.

- [ ] **Step 2: Implement Compose argument construction**

```powershell
function Get-IncipitProfileArguments {
    param([switch]$Full)
    if (-not $Full) { return @() }
    return @('--profile', 'rerank', '--profile', 'ocr', '--profile', 'observe')
}

function Get-IncipitOptionalServices {
    param([switch]$Full)
    if ($Full) { return 'rerank,ocr,observe' }
    return ''
}
```

- [ ] **Step 3: Implement bounded wait helpers**

```powershell
function Wait-IncipitHttp {
    param(
        [string]$Uri,
        [int]$TimeoutSeconds = 180,
        [int[]]$AcceptedStatus = @(200)
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5 -SkipHttpErrorCheck
            if ($response.StatusCode -in $AcceptedStatus) { return $response }
        } catch {}
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for $Uri after $TimeoutSeconds seconds"
}
```

For container health, poll `docker compose ps --format json`, parse every JSON line, and require all requested services to be `running` with health `healthy` when a health check exists. On timeout, automatically print `docker compose ps` and the last 100 log lines for failed services.

`Wait-IncipitInfrastructure` treats core infrastructure timeouts as terminating failures. Under `-Full`, rerank, OCR, and observability timeouts become named warnings; startup continues so `/ready` and `status` can expose a `DEGRADED` system instead of incorrectly blocking the healthy core.

- [ ] **Step 4: Implement orchestration functions**

```powershell
function Start-Incipit {
    [CmdletBinding()]
    param([switch]$Full)

    $doctor = @(Invoke-IncipitDoctor -Full:$Full)
    if ($doctor | Where-Object { $_.Required -and $_.State -eq 'FAIL' }) {
        throw 'Doctor found required failures. Fix them before startup.'
    }

    $profileArgs = @(Get-IncipitProfileArguments -Full:$Full)
    $env:INCIPIT_OPTIONAL_SERVICES = Get-IncipitOptionalServices -Full:$Full

    $arguments = @('compose') + $profileArgs + @('build', 'api', 'web')
    Invoke-IncipitDocker -Arguments $arguments
    $arguments = @('compose') + $profileArgs + @(
        'up', '-d', 'postgres', 'minio', 'etcd', 'milvus',
        'elasticsearch', 'redis', 'temporal', 'temporal-ui'
    )
    Invoke-IncipitDocker -Arguments $arguments
    if ($Full) {
        $arguments = @('compose') + $profileArgs + @(
            'up', '-d', 'infinity', 'paddleocr', 'mineru', 'prometheus', 'grafana', 'phoenix'
        )
        Invoke-IncipitDocker -Arguments $arguments
    }
    Wait-IncipitInfrastructure -Full:$Full
    $arguments = @('compose') + $profileArgs + @('run', '--rm', 'migrate')
    Invoke-IncipitDocker -Arguments $arguments
    $arguments = @('compose') + $profileArgs + @('up', '-d', 'api')
    Invoke-IncipitDocker -Arguments $arguments
    Wait-IncipitHttp -Uri "http://127.0.0.1:$($env:API_HOST_PORT ?? '8000')/ready" -AcceptedStatus @(200)
    $arguments = @('compose') + $profileArgs + @('up', '-d', 'worker')
    Invoke-IncipitDocker -Arguments $arguments
    Wait-IncipitWorker
    $arguments = @('compose') + $profileArgs + @('up', '-d', 'web')
    Invoke-IncipitDocker -Arguments $arguments
    Wait-IncipitHttp -Uri "http://127.0.0.1:$($env:WEB_HOST_PORT ?? '3300')/"
    Get-IncipitStatus
}

function Stop-Incipit {
    Invoke-IncipitDocker @('compose', 'down', '--remove-orphans')
}
```

PowerShell array concatenation is always computed into `$arguments` before calling `Invoke-IncipitDocker`, avoiding ambiguous parameter binding.

- [ ] **Step 5: Implement status and logs**

`Get-IncipitStatus` combines:

- `docker compose ps --format json` container state.
- `GET /health` process state.
- `GET /ready` dependency state and details.
- `runtime_probe.py worker --json` poller state.
- `GET /` web state.

Output one summary line first, for example `INCIPIT: DEGRADED`, followed by a compact table. `-Json` returns:

```json
{
  "status": "DEGRADED",
  "checks": [
    {"name": "api", "required": true, "state": "PASS", "detail": "HTTP 200"},
    {"name": "llm", "required": false, "state": "WARN", "detail": "connection refused"}
  ]
}
```

`Show-IncipitLogs -Service api -Follow` maps to `docker compose logs --tail 200 --follow api`. Without `-Service`, show the last 100 lines from `api`, `worker`, `web`, `temporal`, and any unhealthy container.

- [ ] **Step 6: Add the stable dispatcher**

```powershell
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('doctor', 'start', 'stop', 'status', 'logs', 'smoke')]
    [string]$Command = 'status',
    [switch]$Full,
    [switch]$Json,
    [switch]$Follow,
    [string]$Service,
    [ValidateSet('L0', 'L1')]
    [string]$Level = 'L1'
)

$ErrorActionPreference = 'Stop'
Import-Module "$PSScriptRoot/scripts/runtime/Incipit.Runtime.psm1" -Force

switch ($Command) {
    'doctor' { Invoke-IncipitDoctor -Json:$Json -Full:$Full }
    'start'  { Start-Incipit -Full:$Full }
    'stop'   { Stop-Incipit }
    'status' { Get-IncipitStatus -Json:$Json }
    'logs'   { Show-IncipitLogs -Service $Service -Follow:$Follow }
    'smoke'  { Invoke-IncipitSmoke -Level $Level }
}
```

- [ ] **Step 7: Verify tests and a real core startup**

```powershell
Invoke-Pester tests/runtime/incipit.Tests.ps1 -Output Detailed
./incipit.ps1 doctor
./incipit.ps1 start
./incipit.ps1 status -Json
docker compose exec -T api python scripts/runtime/runtime_probe.py worker --json
```

Expected: doctor has no required failure, status is `HEALTHY` or `DEGRADED` only for optional model checks, and the worker probe exits 0.

- [ ] **Step 8: Verify stop preserves named volumes**

```powershell
$before = docker volume ls --filter label=com.docker.compose.project=incipit --format '{{.Name}}' | Sort-Object
./incipit.ps1 stop
$after = docker volume ls --filter label=com.docker.compose.project=incipit --format '{{.Name}}' | Sort-Object
Compare-Object $before $after
```

Expected: `Compare-Object` produces no output.

- [ ] **Step 9: Commit in `arc-knowledge-ai`**

```powershell
git add incipit.ps1 scripts/runtime/Incipit.Runtime.psm1 tests/runtime/incipit.Tests.ps1
git commit -m "feat: add local runtime control commands"
```

---

## Task 7: Implement L0 and L1 Smoke Verification

**Files:**
- Create: `scripts/runtime/smoke.py`
- Create: `tests/fixtures/r0-smoke.md`
- Create: `tests/unit/scripts/test_runtime_smoke.py`
- Modify: `scripts/runtime/Incipit.Runtime.psm1`
- Modify: `tests/runtime/incipit.Tests.ps1`

**Concept to explain:** Health checks prove components; smoke tests prove a user journey. L1 intentionally crosses API, Temporal, object storage, database, vector search, keyword search, model generation, and citations.

**Interfaces:**

```text
smoke.py --level l0 --api-base-url http://api:8000 --web-base-url http://web
smoke.py --level l1 --api-base-url http://api:8000 --web-base-url http://web

L0: /health, /ready, web HTTP, Temporal worker poller
L1: auth -> space -> upload -> workflow COMPLETED -> search -> chat SSE -> citation -> cleanup
```

- [ ] **Step 1: Add deterministic smoke content**

```markdown
# R0 Runtime Verification

The Incipit R0 verification codeword is ORCHID-7429.
This sentence exists only to prove that ingestion, retrieval, generation, and citations work together.
```

- [ ] **Step 2: Add failing tests for polling and SSE parsing**

```python
import pytest

from scripts.runtime.smoke import SmokeFailure, parse_sse, wait_for_document


def test_parse_sse_collects_answer_and_citations():
    events = parse_sse([
        'data: {"delta":"ORCHID-7429"}', '',
        'data: {"citations":[{"doc_id":"d1"}]}', '',
        'data: [DONE]', '',
    ])
    assert events.answer == "ORCHID-7429"
    assert events.citations[0]["doc_id"] == "d1"


class ResponseStub:
    status_code = 200

    def json(self):
        return {"workflow_status": "FAILED"}

    def raise_for_status(self):
        return None


class ClientStub:
    async def get(self, path):
        return ResponseStub()


@pytest.mark.asyncio
async def test_wait_for_document_rejects_failed_workflow():
    with pytest.raises(SmokeFailure, match="FAILED"):
        await wait_for_document(ClientStub(), "d1", timeout_seconds=1)
```

- [ ] **Step 3: Run and confirm the missing-module failure**

```powershell
uv run pytest tests/unit/scripts/test_runtime_smoke.py -q
```

- [ ] **Step 4: Implement typed smoke primitives**

```python
from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from uuid import uuid4

import httpx


class SmokeFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class SmokeConfig:
    api_base_url: str
    web_base_url: str
    tenant_id: str
    email: str
    password: str
    timeout_seconds: int = 300


@dataclass(frozen=True)
class SseResult:
    answer: str
    citations: list[dict[str, object]]


def parse_sse(lines: list[str]) -> SseResult:
    answer: list[str] = []
    citations: list[dict[str, object]] = []
    for line in lines:
        if not line.startswith("data:"):
            continue
        data = line.partition(":")[2].strip()
        if data == "[DONE]":
            break
        payload = json.loads(data)
        if "error" in payload:
            raise SmokeFailure(str(payload["error"]))
        answer.append(payload.get("delta", ""))
        citations.extend(payload.get("citations", []))
    return SseResult("".join(answer), citations)


async def wait_for_document(client: httpx.AsyncClient, document_id: str, timeout_seconds: int):
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        response = await client.get(f"/documents/{document_id}/status")
        response.raise_for_status()
        status = response.json()["workflow_status"]
        if status == "COMPLETED":
            return
        if status in {"FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}:
            raise SmokeFailure(f"document workflow ended as {status}")
        await asyncio.sleep(2)
    raise SmokeFailure(f"document {document_id} did not complete in {timeout_seconds}s")
```

- [ ] **Step 5: Implement L0 checks**

`run_l0()` must:

1. Request API `/health` and require HTTP 200 plus `status=ok`.
2. Request API `/ready`, require HTTP 200, and record `healthy` or `degraded`.
3. Request web `/` and require HTTP 200 plus an HTML content type.
4. Call `probe_schema()` from `runtime_probe.py` and require every migration-owned table to exist.
5. Call `probe_worker()` from `runtime_probe.py` and require `ok=true`.
6. Print one JSON result with elapsed time per check.

L0 accepts a `degraded` readiness report when every required check passes. L1 is the strict gate for LLM and embedding availability.

- [ ] **Step 6: Implement the exact L1 business flow**

Use a single `httpx.AsyncClient(base_url=config.api_base_url)` and these calls:

```python
headers = {"X-Tenant-Id": config.tenant_id}

# Reuse one durable smoke user. A 409 register response is expected after the first run.
register = await client.post(
    "/auth/register",
    headers=headers,
    json={"email": config.email, "password": config.password},
)
if register.status_code not in {201, 409}:
    register.raise_for_status()

login = await client.post(
    "/auth/login",
    headers=headers,
    json={"email": config.email, "password": config.password},
)
login.raise_for_status()
headers["Authorization"] = f"Bearer {login.json()['access_token']}"

run_id = uuid4().hex[:12]
space = await client.post(
    "/spaces",
    headers=headers,
    json={"name": f"R0 smoke {run_id}"},
)
space.raise_for_status()
space_id = space.json()["space_id"]

fixture = Path("tests/fixtures/r0-smoke.md")
with fixture.open("rb") as handle:
    upload = await client.post(
        "/documents/upload",
        params={"space_id": space_id},
        headers=headers,
        files={"file": (fixture.name, handle, "text/markdown")},
    )
upload.raise_for_status()
document_id = upload.json()["document_id"]
```

Then:

- Poll `/documents/{document_id}/status` until `COMPLETED`.
- Call `/search` with `q=ORCHID-7429`, `space_id=<space_id>`, `top_k=5`, and `score_threshold=0`; require at least one hit or chunk whose content includes `ORCHID-7429`.
- Create a session with `POST /sessions` and `{title: "R0 smoke", space_id}`.
- Stream `POST /chat` with the question `What is the Incipit R0 verification codeword?`, the created session and space IDs, `top_k=5`, and `score_threshold=0`.
- Require the assembled answer to contain `ORCHID-7429`.
- Require at least one citation whose `doc_id` equals the uploaded document ID.
- In a `finally` block, delete the document and call `DELETE /spaces/{space_id}`. The current space API archives rather than physically erases the space; this is the intentional logical cleanup boundary. Never delete data not created by the current smoke run.

- [ ] **Step 7: Add CLI and failure reporting**

```python
def config_from_env(args) -> SmokeConfig:
    return SmokeConfig(
        api_base_url=args.api_base_url,
        web_base_url=args.web_base_url,
        tenant_id=os.environ.get("SMOKE_TENANT_ID", "r0-smoke"),
        email=os.environ.get("SMOKE_EMAIL", "r0-smoke@example.com"),
        password=os.environ["SMOKE_PASSWORD"],
        timeout_seconds=args.timeout_seconds,
    )
```

The CLI must hide the password, print the failed stage and resource IDs, include a response Trace ID only when one is present, and return exit 1 for `SmokeFailure` or HTTP failure. `--level l1` runs L0 first. A cleanup failure is reported as a separate warning after the primary result and never broadens the deletion scope.

- [ ] **Step 8: Wire PowerShell smoke execution**

```powershell
function Invoke-IncipitSmoke {
    [CmdletBinding()]
    param([ValidateSet('L0', 'L1')][string]$Level = 'L1')
    $arguments = @(
        'compose', 'exec', '-T', 'api',
        'python', 'scripts/runtime/smoke.py',
        '--level', $Level.ToLowerInvariant(),
        '--api-base-url', 'http://api:8000',
        '--web-base-url', 'http://web'
    )
    Invoke-IncipitDocker -Arguments $arguments
    if ($LASTEXITCODE -ne 0) { throw "Smoke $Level failed" }
}
```

Ensure `SMOKE_TENANT_ID`, `SMOKE_EMAIL`, and `SMOKE_PASSWORD` are passed to the `api` container through the Compose application environment mapping.

- [ ] **Step 9: Run unit and live smoke tests**

```powershell
uv run pytest tests/unit/scripts/test_runtime_smoke.py -q
Invoke-Pester tests/runtime/incipit.Tests.ps1 -Output Detailed
./incipit.ps1 start
./incipit.ps1 smoke -Level L0
./incipit.ps1 smoke -Level L1
```

Expected: both smoke levels exit 0; a second L1 run also succeeds using the existing smoke user.

- [ ] **Step 10: Commit in `arc-knowledge-ai`**

```powershell
git add scripts/runtime/smoke.py scripts/runtime/Incipit.Runtime.psm1 tests/fixtures/r0-smoke.md tests/unit/scripts/test_runtime_smoke.py tests/runtime/incipit.Tests.ps1 docker-compose.yml
git commit -m "test: add end-to-end runtime smoke checks"
```

---

## Task 8: Add Runtime Contracts to Both CI Pipelines

**Files:**
- Create: `arc-knowledge-ai/.github/workflows/ci.yml`
- Modify: `arc-knowledge-web/.github/workflows/ci.yml`

**Concept to explain:** CI cannot prove the user's local model configuration, but it can prevent broken lockfiles, invalid Compose, bad PowerShell state logic, and unbuildable production images from reaching the local runtime.

- [ ] **Step 1: Add backend CI with four independent gates**

```yaml
name: Backend CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          version: "0.12.5"
      - run: uv sync --extra dev --frozen
      - run: uv run ruff check app tests scripts
      - run: uv run pytest tests/unit tests/runtime/test_backend_container_contract.py tests/runtime/test_compose_contract.py -q

  compose:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: cp .env.example .env
      - run: docker compose config --quiet
      - run: docker compose --profile rerank --profile ocr --profile observe config --quiet

  image:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: false
          tags: incipit-api:ci

  powershell:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install Pester
        shell: pwsh
        run: Install-Module Pester -Force -Scope CurrentUser -MinimumVersion 5.6.1
      - name: Run runtime command tests
        shell: pwsh
        run: Invoke-Pester tests/runtime/incipit.Tests.ps1 -CI -Output Detailed
```

- [ ] **Step 2: Validate backend workflow syntax locally**

```powershell
uv run python -c "import pathlib,yaml; yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text())"
docker compose config --quiet
```

- [ ] **Step 3: Extend the existing frontend CI without replacing its current test jobs**

Add this job and make the workflow trigger match the repository's actual default branch:

```yaml
  production-image:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: false
          tags: incipit-web:ci
      - name: Validate Nginx configuration
        run: |
          docker build -t incipit-web:ci .
          docker run --rm incipit-web:ci nginx -t
```

- [ ] **Step 4: Run the same local gates CI will run**

Backend:

```powershell
uv run ruff check app tests scripts
uv run pytest tests/unit tests/runtime/test_backend_container_contract.py tests/runtime/test_compose_contract.py -q
Invoke-Pester tests/runtime/incipit.Tests.ps1 -Output Detailed
docker build --tag incipit-api:ci .
```

Frontend:

```powershell
npm test
npm run build
docker build --tag incipit-web:ci .
docker run --rm incipit-web:ci nginx -t
```

- [ ] **Step 5: Commit separately**

Backend:

```powershell
git add .github/workflows/ci.yml
git commit -m "ci: verify backend runtime contracts"
```

Frontend:

```powershell
git add .github/workflows/ci.yml
git commit -m "ci: build production web image"
```

---

## Task 9: Document, Fault-Drill, and Accept R0

**Files:**
- Modify: `README.md`
- Create: `docs/operations/local-runtime.md`
- Modify: `docs/superpowers/specs/2026-09-01-r0-local-runtime-design.md` only if implementation revealed an approved design correction

**Concept to explain:** Operations documentation should begin with observable states and recovery actions. A command list without failure interpretation is not an operational runbook.

- [ ] **Step 1: Add the README quick start**

The first local-run section must contain only this happy path before deeper explanation:

```powershell
Copy-Item .env.example .env
./incipit.ps1 doctor
./incipit.ps1 start
./incipit.ps1 smoke -Level L1
```

Then state the URLs derived from default ports:

- Web: `http://127.0.0.1:3300`
- API docs: `http://127.0.0.1:8000/docs`
- Temporal UI: `http://127.0.0.1:8233`

- [ ] **Step 2: Write the operational runbook**

Use these headings and concrete recovery commands:

```markdown
# Local Runtime Operations

## Prerequisites
## Configuration and model endpoints
## Core versus full startup
## HEALTHY, DEGRADED, UNHEALTHY, and STOPPED
## Doctor checks
## Status and logs
## L0 and L1 smoke tests
## Restart after Windows reboot
## Port conflict recovery
## Model endpoint recovery
## Container and volume recovery
## Data preservation and backup boundary
## Known R0 limitations and R0.5 clean-machine verification
```

For a port conflict, document editing the matching `*_HOST_PORT` in `.env`, then running `./incipit.ps1 start` again. For model recovery, document `host.docker.internal`, Ollama's listen address, and `./incipit.ps1 status`/`smoke` verification. Explicitly say that `stop` preserves volumes and that deleting Docker volumes is outside the R0 command surface.

- [ ] **Step 3: Run safe fault drills**

Perform each drill and capture the observed status in the implementation notes:

1. With a mocked unavailable Docker daemon, Pester proves `doctor` emits a required failure. Do not stop the user's real Docker Desktop.
2. Temporarily set `LLM_BASE_URL=http://host.docker.internal:1/v1`, restart `api`, and verify `/ready` is HTTP 200 with `degraded`; restore `.env` immediately afterward.
3. Temporarily stop Redis using `docker compose stop redis`, verify `/ready` is HTTP 503 with `unhealthy`, then run `docker compose start redis` and wait for recovery.
4. Run `./incipit.ps1 stop`, restart, and verify the second L1 smoke succeeds while named volume names remain unchanged.

Also prove migration behavior on an empty disposable PostgreSQL instance. Use the exact names `incipit-r0-migration-test` and `incipit-r0-postgres-test`, verify those names before stopping them, and never target the normal `incipit` project:

```powershell
docker network create incipit-r0-migration-test
docker run -d --rm --name incipit-r0-postgres-test --network incipit-r0-migration-test -e POSTGRES_USER=arc -e POSTGRES_PASSWORD=arc -e POSTGRES_DB=arc_knowledge postgres:16.3-alpine
docker run --rm --network incipit-r0-migration-test -e POSTGRES_URL=postgresql+asyncpg://arc:arc@incipit-r0-postgres-test:5432/arc_knowledge incipit-api:r0 python scripts/migrate.py
docker run --rm --network incipit-r0-migration-test -e POSTGRES_URL=postgresql+asyncpg://arc:arc@incipit-r0-postgres-test:5432/arc_knowledge incipit-api:r0 python scripts/migrate.py
docker stop incipit-r0-postgres-test
docker network rm incipit-r0-migration-test
```

Expected: both migration runs exit 0. The disposable container uses `--rm`; stopping it removes only its anonymous test data.

- [ ] **Step 4: Run the complete acceptance matrix**

```powershell
# Backend static and unit gates
uv run ruff check app tests scripts
uv run pytest tests/unit tests/runtime/test_backend_container_contract.py tests/runtime/test_compose_contract.py -q
Invoke-Pester tests/runtime/incipit.Tests.ps1 -Output Detailed

# Frontend gates
Push-Location ../arc-knowledge-web
npm test
npm run build
docker build --tag incipit-web:r0 .
docker run --rm incipit-web:r0 nginx -t
Pop-Location

# Runtime gates
docker build --tag incipit-api:r0 .
docker compose config --quiet
./incipit.ps1 doctor
./incipit.ps1 start
./incipit.ps1 start
./incipit.ps1 status -Json
./incipit.ps1 smoke -Level L0
./incipit.ps1 smoke -Level L1
./incipit.ps1 stop
```

Expected: all static/unit/image checks pass; live status contains no required failure; both smoke levels exit 0; stop leaves volumes intact.

- [ ] **Step 5: Review implementation against every R0 design requirement**

Check off this coverage list in the implementation task notes:

- One command starts the core system after reboot.
- `-Full` enables rerank, OCR, MinerU, and observability profiles.
- Schema migration precedes API/worker startup.
- API and web production images build from locked dependencies.
- Core health failures are unhealthy; model failures are degraded.
- Worker state comes from a Temporal task-queue poller.
- `doctor`, `status`, `logs`, `stop`, L0, and L1 behave as documented.
- Duplicate fixed host ports are configurable and diagnosed.
- Offline model/cache assumptions are explicit; startup performs no surprise model download.
- Stop/restart preserves PostgreSQL, MinIO, Milvus, Elasticsearch, Redis, Temporal, and observability volumes.
- Backend and frontend CI cover their respective runtime artifacts.
- R0 limitations clearly defer clean-machine proof and formal backups to later milestones.

- [ ] **Step 6: Commit documentation in `arc-knowledge-ai`**

```powershell
git add README.md docs/operations/local-runtime.md docs/superpowers/specs/2026-09-01-r0-local-runtime-design.md
git commit -m "docs: add local runtime operations guide"
```

- [ ] **Step 7: Invoke verification-before-completion and inspect both repositories**

```powershell
git status --short
git log --oneline -8
Push-Location ../arc-knowledge-web
git status --short
git log --oneline -5
Pop-Location
```

Do not claim R0 complete until the fresh outputs from Step 4 are present and both repositories contain only expected changes.

---

## Execution Notes

- Recommended execution mode is `superpowers:subagent-driven-development`: one implementation task at a time, with spec review and code-quality review after each task.
- If executing inline in a separate session, use `superpowers:executing-plans` and stop at the end of Tasks 3, 6, and 9 for user-visible checkpoints.
- Use `superpowers:systematic-debugging` for any failed build, health probe, migration, or smoke stage. Do not weaken a check merely to make it green.
- Use `superpowers:verification-before-completion` immediately before reporting R0 as finished.
