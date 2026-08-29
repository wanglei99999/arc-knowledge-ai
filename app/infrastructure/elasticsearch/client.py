from __future__ import annotations

import asyncio

from elasticsearch import Elasticsearch, NotFoundError

from app.config.settings import settings
from app.domain.metadata_filter import MetadataFilter
from app.domain.retrieval import normalize_document_ids
from app.infrastructure.elasticsearch.filter_builder import build_es_clauses

INDEX_NAME = "arc_chunks"

_MAPPINGS = {
    "mappings": {
        "properties": {
            "chunk_id": {"type": "keyword"},
            "document_id": {"type": "keyword"},
            "tenant_id": {"type": "keyword"},
            "space_id": {"type": "keyword"},
            "chunk_index": {"type": "integer"},
            "content": {"type": "text", "analyzer": "standard"},
            "metadata": {"type": "object", "dynamic": True},
        }
    }
}


def _get_client() -> Elasticsearch:
    return Elasticsearch(settings.es_url)


def _ensure_index(client: Elasticsearch) -> None:
    if not client.indices.exists(index=INDEX_NAME):
        client.indices.create(index=INDEX_NAME, body=_MAPPINGS)


async def reset_index() -> int:
    """删除并重建 arc_chunks 索引（开发环境清盘用）。返回删除前的文档数量。"""

    def _reset() -> int:
        client = _get_client()
        count = 0
        if client.indices.exists(index=INDEX_NAME):
            stats = client.indices.stats(index=INDEX_NAME)
            count = stats["indices"][INDEX_NAME]["total"]["docs"]["count"]
            client.indices.delete(index=INDEX_NAME)
        client.indices.create(index=INDEX_NAME, body=_MAPPINGS)
        return count

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _reset)


async def index_chunks(chunks: list[dict]) -> None:
    """
    批量写入 chunks 到 ES，供 BM25 全文检索。
    chunks 格式：[{chunk_id, document_id, tenant_id, chunk_index, content}]
    """
    if not chunks:
        return

    def _index() -> None:
        client = _get_client()
        _ensure_index(client)
        for chunk in chunks:
            client.index(
                index=INDEX_NAME,
                id=chunk["chunk_id"],
                document={
                    "chunk_id": chunk["chunk_id"],
                    "document_id": chunk["document_id"],
                    "tenant_id": chunk["tenant_id"],
                    "space_id": chunk["space_id"],
                    "chunk_index": chunk["chunk_index"],
                    "content": chunk["content"],
                    "metadata": chunk.get("metadata", {}),
                },
            )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _index)


def _build_search_filters(
    tenant_id: str,
    space_id: str,
    metadata_filters: list[MetadataFilter] | None = None,
    document_ids: list[str] | None = None,
) -> list[dict]:
    """拼接 tenant/space 硬隔离、文档范围与可选 metadata 子句。"""
    clauses: list[dict] = [
        {"term": {"tenant_id": tenant_id}},
        {"term": {"space_id": space_id}},
    ]
    canonical_ids = normalize_document_ids(document_ids)
    if canonical_ids:
        clauses.append({"terms": {"document_id": canonical_ids}})
    if metadata_filters:
        clauses.extend(build_es_clauses(metadata_filters))
    return clauses


async def bm25_search(
    query_text: str,
    tenant_id: str,
    space_id: str,
    top_k: int = 10,
    metadata_filters: list[MetadataFilter] | None = None,
    document_ids: list[str] | None = None,
) -> list[dict]:
    """
    BM25 全文检索，按 tenant_id/space_id 过滤 + 可选 metadata 过滤。
    返回 [{chunk_id, document_id, chunk_index, score}]
    """

    def _search() -> list[dict]:
        client = _get_client()
        try:
            resp = client.search(
                index=INDEX_NAME,
                body={
                    "query": {
                        "bool": {
                            "must": {"match": {"content": query_text}},
                            "filter": _build_search_filters(
                                tenant_id,
                                space_id,
                                metadata_filters=metadata_filters,
                                document_ids=document_ids,
                            ),
                        }
                    },
                    "size": top_k,
                },
            )
        except NotFoundError:
            return []
        return [
            {
                "chunk_id": hit["_source"]["chunk_id"],
                "document_id": hit["_source"]["document_id"],
                "chunk_index": hit["_source"]["chunk_index"],
                "score": hit["_score"],
            }
            for hit in resp["hits"]["hits"]
        ]

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _search)


async def delete_by_document(document_id: str, tenant_id: str) -> None:
    """删除某文档的全部 ES 记录（重新入库时使用）"""

    def _delete() -> None:
        client = _get_client()
        client.delete_by_query(
            index=INDEX_NAME,
            body={
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"document_id": document_id}},
                            {"term": {"tenant_id": tenant_id}},
                        ]
                    }
                }
            },
        )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _delete)
