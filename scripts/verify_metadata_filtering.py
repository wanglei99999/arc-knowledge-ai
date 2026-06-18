"""端到端验证：metadata filtering 在真实 Milvus / ES 上生效。

前置：docker compose up -d etcd minio milvus elasticsearch
运行：python scripts/verify_metadata_filtering.py

不调用 LLM / Embedding：用构造的固定向量与文本直接验证整条链路——
「存储层 metadata 字段 + 过滤编译器（DSL→Milvus 表达式 / ES query）+ 检索 client」。
存在失败项时以非零退出码结束，可用作 CI 冒烟。

验证矩阵（Milvus 与 ES 各一套）：
  无过滤        → 命中全部 3 条
  eq（字符串）  → project==A → 2 条
  gte（数值）   → year>=2024 → 2 条（验证 JSON/object 的数值范围，非字符串比较）
  in / nin      → 集合操作 → 1 条
"""

from __future__ import annotations

import asyncio

from app.domain.metadata_filter import FilterOperator, MetadataFilter
from app.infrastructure.elasticsearch import client as es
from app.infrastructure.milvus import client as milvus

TENANT = "verify_tenant"
SPACE = "verify_space"
DIM = 8
VEC = [1.0] + [0.0] * (DIM - 1)  # 所有记录共用，确保 cosine 命中，过滤交给 metadata


def _f(key: str, op: FilterOperator, value) -> list[MetadataFilter]:
    return [MetadataFilter(key=key, operator=op, value=value)]


async def _poll(fn, expected: int, tries: int = 15, delay: float = 1.0) -> list[dict]:
    """轮询直到命中数达到 expected（Milvus/ES 写入有最终一致延迟）。"""
    hits: list[dict] = []
    for _ in range(tries):
        hits = await fn()
        if len(hits) == expected:
            return hits
        await asyncio.sleep(delay)
    return hits


def _check(name: str, hits: list[dict], expected_ids: list[str]) -> bool:
    got = sorted(h["chunk_id"] for h in hits)
    exp = sorted(expected_ids)
    ok = got == exp
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: 期望 {exp}，实际 {got}")
    return ok


async def verify_milvus() -> bool:
    print("== Milvus ==")
    records = [
        milvus.VectorRecord("v1", "doc1", TENANT, SPACE, 0, VEC, {"project": "A", "year": 2023}),
        milvus.VectorRecord("v2", "doc2", TENANT, SPACE, 0, VEC, {"project": "A", "year": 2024}),
        milvus.VectorRecord("v3", "doc3", TENANT, SPACE, 0, VEC, {"project": "B", "year": 2024}),
    ]
    await milvus.insert_vectors(records)

    async def search(filters: list[MetadataFilter] | None = None) -> list[dict]:
        return await milvus.search_vectors(
            VEC, TENANT, SPACE, top_k=10, score_threshold=0.0, metadata_filters=filters
        )

    base = await _poll(search, 3)
    return all(
        [
            _check("无过滤", base, ["v1", "v2", "v3"]),
            _check("project==A", await search(_f("project", FilterOperator.EQ, "A")), ["v1", "v2"]),
            _check("year>=2024", await search(_f("year", FilterOperator.GTE, 2024)), ["v2", "v3"]),
            _check("project in [B]", await search(_f("project", FilterOperator.IN, ["B"])), ["v3"]),
        ]
    )


async def verify_es() -> bool:
    print("== Elasticsearch ==")
    content = "metadata filtering verification chunk"

    def _chunk(cid: str, did: str, meta: dict) -> dict:
        return {
            "chunk_id": cid,
            "document_id": did,
            "tenant_id": TENANT,
            "space_id": SPACE,
            "chunk_index": 0,
            "content": content,
            "metadata": meta,
        }

    await es.index_chunks(
        [
            _chunk("e1", "doc1", {"project": "A", "year": 2023}),
            _chunk("e2", "doc2", {"project": "A", "year": 2024}),
            _chunk("e3", "doc3", {"project": "B", "year": 2024}),
        ]
    )

    async def search(filters: list[MetadataFilter] | None = None) -> list[dict]:
        return await es.bm25_search(
            "verification", TENANT, SPACE, top_k=10, metadata_filters=filters
        )

    base = await _poll(search, 3)
    return all(
        [
            _check("无过滤", base, ["e1", "e2", "e3"]),
            _check("project==A", await search(_f("project", FilterOperator.EQ, "A")), ["e1", "e2"]),
            _check("year>=2024", await search(_f("year", FilterOperator.GTE, 2024)), ["e2", "e3"]),
            _check(
                "project nin [A]", await search(_f("project", FilterOperator.NIN, ["A"])), ["e3"]
            ),
        ]
    )


async def main() -> None:
    print("重置搜索存储（应用新 schema）...")
    await milvus.reset_collection()
    await es.reset_index()

    ok_milvus = await verify_milvus()
    ok_es = await verify_es()

    # 清理验证数据
    for doc in ("doc1", "doc2", "doc3"):
        await milvus.delete_by_document(doc, TENANT)
        await es.delete_by_document(doc, TENANT)

    print()
    if ok_milvus and ok_es:
        print("✅ 全部通过：metadata filtering 在 Milvus + ES 上端到端生效。")
    else:
        print("❌ 存在失败项，见上方 FAIL。")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
