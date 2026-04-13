from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, MilvusClient

from app.config.settings import settings

# Collection 名称
COLLECTION_NAME = "arc_chunk_embeddings"

# 字段名
FIELD_CHUNK_ID = "chunk_id"
FIELD_DOCUMENT_ID = "document_id"
FIELD_TENANT_ID = "tenant_id"
FIELD_CHUNK_INDEX = "chunk_index"
FIELD_EMBEDDING = "embedding"

# 默认向量维度（text-embedding-3-small）
DEFAULT_DIM = 1536


def _get_client() -> MilvusClient:
    return MilvusClient(
        uri=f"http://{settings.milvus_host}:{settings.milvus_port}"
    )


def _ensure_collection(client: MilvusClient, dim: int) -> None:
    """创建 Collection（如果不存在）。tenant_id 作为 Partition Key 实现租户隔离。"""
    if client.has_collection(COLLECTION_NAME):
        return

    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(FIELD_CHUNK_ID, DataType.VARCHAR, max_length=64, is_primary=True)
    schema.add_field(FIELD_DOCUMENT_ID, DataType.VARCHAR, max_length=64)
    schema.add_field(FIELD_TENANT_ID, DataType.VARCHAR, max_length=64, is_partition_key=True)
    schema.add_field(FIELD_CHUNK_INDEX, DataType.INT32)
    schema.add_field(FIELD_EMBEDDING, DataType.FLOAT_VECTOR, dim=dim)

    index_params = MilvusClient.prepare_index_params()
    index_params.add_index(
        field_name=FIELD_EMBEDDING,
        index_type="HNSW",
        metric_type="COSINE",
        params={"M": 16, "efConstruction": 200},
    )

    client.create_collection(
        collection_name=COLLECTION_NAME,
        schema=schema,
        index_params=index_params,
    )


@dataclass
class VectorRecord:
    chunk_id: str
    document_id: str
    tenant_id: str
    chunk_index: int
    embedding: list[float]


async def insert_vectors(records: list[VectorRecord]) -> None:
    """批量写入向量到 Milvus。"""
    if not records:
        return

    dim = len(records[0].embedding)

    def _insert() -> None:
        client = _get_client()
        _ensure_collection(client, dim)
        data = [
            {
                FIELD_CHUNK_ID: r.chunk_id,
                FIELD_DOCUMENT_ID: r.document_id,
                FIELD_TENANT_ID: r.tenant_id,
                FIELD_CHUNK_INDEX: r.chunk_index,
                FIELD_EMBEDDING: r.embedding,
            }
            for r in records
        ]
        client.upsert(collection_name=COLLECTION_NAME, data=data)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _insert)


async def delete_by_document(document_id: str, tenant_id: str) -> None:
    """删除某文档的全部向量（用于重新入库）。"""
    def _delete() -> None:
        client = _get_client()
        client.delete(
            collection_name=COLLECTION_NAME,
            filter=f'{FIELD_DOCUMENT_ID} == "{document_id}" and {FIELD_TENANT_ID} == "{tenant_id}"',
        )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _delete)


async def search_vectors(
    query_vector: list[float],
    tenant_id: str,
    top_k: int = 10,
    score_threshold: float = 0.5,
) -> list[dict]:
    """
    ANN 向量检索，按 tenant_id Partition Key 隔离。
    返回 [{ chunk_id, document_id, chunk_index, score }, ...]
    """
    def _search() -> list[dict]:
        client = _get_client()
        results = client.search(
            collection_name=COLLECTION_NAME,
            data=[query_vector],
            filter=f'{FIELD_TENANT_ID} == "{tenant_id}"',
            limit=top_k,
            output_fields=[FIELD_CHUNK_ID, FIELD_DOCUMENT_ID, FIELD_CHUNK_INDEX],
            search_params={"metric_type": "COSINE", "params": {"ef": 100}},
        )
        hits = []
        for hit in results[0]:
            if hit["distance"] >= score_threshold:
                hits.append({
                    "chunk_id": hit["entity"][FIELD_CHUNK_ID],
                    "document_id": hit["entity"][FIELD_DOCUMENT_ID],
                    "chunk_index": hit["entity"][FIELD_CHUNK_INDEX],
                    "score": hit["distance"],
                })
        return hits

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _search)
