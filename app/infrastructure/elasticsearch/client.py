from __future__ import annotations

import asyncio

from elasticsearch import Elasticsearch, NotFoundError

from app.config.settings import settings

INDEX_NAME = "arc_chunks"

_MAPPINGS = {
    "mappings": {
        "properties": {
            "chunk_id":    {"type": "keyword"},
            "document_id": {"type": "keyword"},
            "tenant_id":   {"type": "keyword"},
            "chunk_index": {"type": "integer"},
            "content":     {"type": "text", "analyzer": "standard"},
        }
    }
}


def _get_client() -> Elasticsearch:
    return Elasticsearch(settings.es_url)


def _ensure_index(client: Elasticsearch) -> None:
    if not client.indices.exists(index=INDEX_NAME):
        client.indices.create(index=INDEX_NAME, body=_MAPPINGS)


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
                    "chunk_id":    chunk["chunk_id"],
                    "document_id": chunk["document_id"],
                    "tenant_id":   chunk["tenant_id"],
                    "chunk_index": chunk["chunk_index"],
                    "content":     chunk["content"],
                },
            )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _index)


async def bm25_search(
    query_text: str,
    tenant_id: str,
    top_k: int = 10,
) -> list[dict]:
    """
    BM25 全文检索，按 tenant_id 过滤。
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
                            "must":   {"match": {"content": query_text}},
                            "filter": {"term": {"tenant_id": tenant_id}},
                        }
                    },
                    "size": top_k,
                },
            )
        except NotFoundError:
            return []
        return [
            {
                "chunk_id":    hit["_source"]["chunk_id"],
                "document_id": hit["_source"]["document_id"],
                "chunk_index": hit["_source"]["chunk_index"],
                "score":       hit["_score"],
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
                            {"term": {"tenant_id":   tenant_id}},
                        ]
                    }
                }
            },
        )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _delete)
