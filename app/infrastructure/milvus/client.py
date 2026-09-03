from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from pymilvus import DataType, MilvusClient

from app.config.settings import settings
from app.domain.metadata_filter import MetadataFilter
from app.domain.retrieval import normalize_document_ids
from app.infrastructure.milvus.filter_builder import build_milvus_expr

# Collection 名称
COLLECTION_NAME = "arc_chunk_embeddings"

# 字段名
FIELD_CHUNK_ID = "chunk_id"
FIELD_DOCUMENT_ID = "document_id"
FIELD_TENANT_ID = "tenant_id"
FIELD_SPACE_ID = "space_id"
FIELD_CHUNK_INDEX = "chunk_index"
FIELD_METADATA = "metadata"
FIELD_EMBEDDING = "embedding"

# 默认向量维度（text-embedding-v3 / text-embedding-v4 = 1024）
DEFAULT_DIM = 1024


def _get_client() -> MilvusClient:
    return MilvusClient(uri=f"http://{settings.milvus_host}:{settings.milvus_port}")


def _ensure_collection(client: MilvusClient, dim: int) -> None:
    """创建 Collection（如果不存在）。tenant_id 作为 Partition Key 实现租户隔离。"""
    if client.has_collection(COLLECTION_NAME):
        return

    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(FIELD_CHUNK_ID, DataType.VARCHAR, max_length=64, is_primary=True)
    schema.add_field(FIELD_DOCUMENT_ID, DataType.VARCHAR, max_length=64)
    schema.add_field(FIELD_TENANT_ID, DataType.VARCHAR, max_length=64, is_partition_key=True)
    schema.add_field(FIELD_SPACE_ID, DataType.VARCHAR, max_length=128)
    schema.add_field(FIELD_CHUNK_INDEX, DataType.INT32)
    schema.add_field(FIELD_METADATA, DataType.JSON)
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
    space_id: str
    chunk_index: int
    embedding: list[float]
    metadata: dict = field(default_factory=dict)


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
                FIELD_SPACE_ID: r.space_id,
                FIELD_CHUNK_INDEX: r.chunk_index,
                FIELD_METADATA: r.metadata,
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


async def reset_collection() -> int:
    """
    删除并重建 arc_chunk_embeddings collection（开发环境清盘用）。
    返回删除前的实体数量（0 表示 collection 不存在）。
    """

    def _reset() -> int:
        client = _get_client()
        count = 0
        if client.has_collection(COLLECTION_NAME):
            stats = client.get_collection_stats(COLLECTION_NAME)
            count = int(stats.get("row_count", 0))
            client.drop_collection(COLLECTION_NAME)
        # 不在此处预建表；首次 insert_vectors 会以实际 embedding 维度建表，
        # 避免硬编码 DEFAULT_DIM 与当前 embedding 模型维度不一致。
        return count

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _reset)


def _build_search_filter(
    tenant_id: str,
    space_id: str,
    metadata_filters: list[MetadataFilter] | None = None,
    document_ids: list[str] | None = None,
) -> str:
    """拼接 tenant/space 硬隔离、文档范围与可选 metadata 条件。"""
    clauses = [
        f'{FIELD_TENANT_ID} == "{tenant_id}"',
        f'{FIELD_SPACE_ID} == "{space_id}"',
    ]
    canonical_ids = normalize_document_ids(document_ids)
    if canonical_ids:
        values = ", ".join(f'"{document_id}"' for document_id in canonical_ids)
        clauses.append(f"{FIELD_DOCUMENT_ID} in [{values}]")
    if metadata_filters:
        meta = build_milvus_expr(metadata_filters)
        if meta:
            clauses.append(meta)
    return " and ".join(clauses)


async def search_vectors(
    query_vector: list[float],
    tenant_id: str,
    space_id: str,
    top_k: int = 10,
    score_threshold: float = 0.5,
    metadata_filters: list[MetadataFilter] | None = None,
    document_ids: list[str] | None = None,
) -> list[dict]:
    """
    ANN 向量检索，按 tenant_id Partition Key 隔离 + 可选 metadata 过滤。
    返回 [{ chunk_id, document_id, chunk_index, score }, ...]
    """

    def _search() -> list[dict]:
        client = _get_client()
        if not client.has_collection(COLLECTION_NAME):
            return []
        results = client.search(
            collection_name=COLLECTION_NAME,
            data=[query_vector],
            filter=_build_search_filter(
                tenant_id,
                space_id,
                metadata_filters=metadata_filters,
                document_ids=document_ids,
            ),
            limit=top_k,
            output_fields=[FIELD_CHUNK_ID, FIELD_DOCUMENT_ID, FIELD_CHUNK_INDEX],
            search_params={"metric_type": "COSINE", "params": {"ef": 100}},
        )
        hits = []
        for hit in results[0]:
            if hit["distance"] >= score_threshold:
                hits.append(
                    {
                        "chunk_id": hit["entity"][FIELD_CHUNK_ID],
                        "document_id": hit["entity"][FIELD_DOCUMENT_ID],
                        "chunk_index": hit["entity"][FIELD_CHUNK_INDEX],
                        "score": hit["distance"],
                    }
                )
        return hits

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _search)
