from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.infrastructure.postgres.repositories import citation_repo as repo_module
from app.infrastructure.postgres.repositories.citation_repo import CitationRepository


class _Result:
    def __init__(self, rows) -> None:
        self._rows = rows

    def fetchall(self):
        return self._rows


class _DB:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params):
        self.calls.append((" ".join(str(statement).lower().split()), params))
        return _Result(self.rows)


@pytest.mark.asyncio
async def test_get_citations_by_message_ids_requires_tenant_scope(monkeypatch) -> None:
    db = _DB(
        [
            SimpleNamespace(
                _mapping={
                    "message_id": "answer-1",
                    "document_id": "document-1",
                    "chunk_id": "chunk-1",
                    "rank": 1,
                    "score": 0.95,
                    "retrieval_mode": "hybrid",
                    "content_snapshot": "合同金额为 100 万元",
                    "title_snapshot": "contract.pdf",
                }
            )
        ]
    )

    @asynccontextmanager
    async def fake_get_db():
        yield db

    monkeypatch.setattr(repo_module, "get_db", fake_get_db)

    grouped = await CitationRepository().get_by_message_ids(["answer-1"], "tenant-1")

    assert grouped["answer-1"][0]["doc_name"] == "contract.pdf"
    sql, params = db.calls[0]
    assert "tenant_id = :tenant_id" in sql
    assert params == {
        "message_ids": ["answer-1"],
        "tenant_id": "tenant-1",
    }
