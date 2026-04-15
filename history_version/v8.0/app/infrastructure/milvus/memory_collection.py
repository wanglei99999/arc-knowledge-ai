from __future__ import annotations

import asyncio

from pymilvus import DataType, MilvusClient

from app.config.settings import settings

MEMORY_COLLECTION = "arc_memories"

_F_MEMORY_ID = "memory_id"
_F_USER_ID = "user_id"
_F_TENANT_ID = "tenant_id"
_F_EMBEDDING = "embedding"

_MODEL_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "text-embedding-v3": 1024,
    "text-embedding-v4": 1024,
}


def _memory_dim() -> int:
    return _MODEL_DIMS.get(settings.openai_embedding_model, 1536)


def _client() -> MilvusClient:
    return MilvusClient(uri=f"http://{settings.milvus_host}:{settings.milvus_port}")


def _ensure_collection(c: MilvusClient) -> None:
    if c.has_collection(MEMORY_COLLECTION):
        return

    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(_F_MEMORY_ID, DataType.VARCHAR, max_length=64, is_primary=True)
    schema.add_field(_F_USER_ID, DataType.VARCHAR, max_length=64)
    schema.add_field(_F_TENANT_ID, DataType.VARCHAR, max_length=64, is_partition_key=True)
    schema.add_field(_F_EMBEDDING, DataType.FLOAT_VECTOR, dim=_memory_dim())

    idx = MilvusClient.prepare_index_params()
    idx.add_index(
        field_name=_F_EMBEDDING,
        index_type="HNSW",
        metric_type="COSINE",
        params={"M": 16, "efConstruction": 200},
    )
    c.create_collection(
        collection_name=MEMORY_COLLECTION,
        schema=schema,
        index_params=idx,
    )


async def upsert_memory_vector(
    memory_id: str, user_id: str, tenant_id: str, embedding: list[float]
) -> None:
    def _do() -> None:
        c = _client()
        _ensure_collection(c)
        c.upsert(
            collection_name=MEMORY_COLLECTION,
            data=[
                {
                    _F_MEMORY_ID: memory_id,
                    _F_USER_ID: user_id,
                    _F_TENANT_ID: tenant_id,
                    _F_EMBEDDING: embedding,
                }
            ],
        )

    await asyncio.get_event_loop().run_in_executor(None, _do)


async def delete_memory_vector(memory_id: str) -> None:
    def _do() -> None:
        c = _client()
        if not c.has_collection(MEMORY_COLLECTION):
            return
        c.delete(
            collection_name=MEMORY_COLLECTION,
            filter=f'{_F_MEMORY_ID} == "{memory_id}"',
        )

    await asyncio.get_event_loop().run_in_executor(None, _do)


async def search_memory_vectors(
    query_embedding: list[float],
    tenant_id: str,
    user_id: str,
    top_k: int = 5,
    score_threshold: float = 0.6,
) -> list[dict]:
    def _do() -> list[dict]:
        c = _client()
        if not c.has_collection(MEMORY_COLLECTION):
            return []
        results = c.search(
            collection_name=MEMORY_COLLECTION,
            data=[query_embedding],
            filter=f'{_F_TENANT_ID} == "{tenant_id}" and {_F_USER_ID} == "{user_id}"',
            limit=top_k,
            output_fields=[_F_MEMORY_ID, _F_USER_ID],
            search_params={"metric_type": "COSINE", "params": {"ef": 100}},
        )
        return [
            {"memory_id": hit["entity"][_F_MEMORY_ID], "score": hit["distance"]}
            for hit in results[0]
            if hit["distance"] >= score_threshold
        ]

    return await asyncio.get_event_loop().run_in_executor(None, _do)
