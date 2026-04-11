from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class DocumentStatus(Enum):
    """文档处理状态机——所有转换必须显式，禁止跳级"""
    PENDING   = "pending"
    PARSING   = "parsing"
    PARSED    = "parsed"
    CHUNKING  = "chunking"
    CHUNKED   = "chunked"
    EMBEDDING = "embedding"
    INDEXED   = "indexed"
    FAILED    = "failed"
    STALE     = "stale"      # 索引过期，需要 reindex

    # 合法的状态转换表
    _TRANSITIONS: dict  # 只用于类型提示，实际定义在类外

VALID_TRANSITIONS: dict[DocumentStatus, set[DocumentStatus]] = {
    DocumentStatus.PENDING:   {DocumentStatus.PARSING, DocumentStatus.FAILED},
    DocumentStatus.PARSING:   {DocumentStatus.PARSED, DocumentStatus.FAILED},
    DocumentStatus.PARSED:    {DocumentStatus.CHUNKING, DocumentStatus.FAILED},
    DocumentStatus.CHUNKING:  {DocumentStatus.CHUNKED, DocumentStatus.FAILED},
    DocumentStatus.CHUNKED:   {DocumentStatus.EMBEDDING, DocumentStatus.FAILED},
    DocumentStatus.EMBEDDING: {DocumentStatus.INDEXED, DocumentStatus.FAILED},
    DocumentStatus.INDEXED:   {DocumentStatus.STALE},
    DocumentStatus.STALE:     {DocumentStatus.PARSING},   # reindex
    DocumentStatus.FAILED:    {DocumentStatus.PARSING},   # 重试
}


@dataclass
class RawFile:
    """未处理的原始文件，是 Ingestion Pipeline 的入口"""
    file_path: str           # MinIO / 本地路径
    mime_type: str           # "application/pdf" 等
    original_filename: str
    size_bytes: int = 0


@dataclass
class DocumentChunk:
    """文档切片，是向量化和检索的基本单元"""
    document_id: str
    tenant_id: str
    content: str
    chunk_index: int
    token_count: int = 0
    chunk_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict = field(default_factory=dict)
    embedding: list[float] | None = None   # EmbedStage 执行后填充


@dataclass
class Document:
    """文档元数据实体（写入 PostgreSQL）"""
    document_id: str
    tenant_id: str
    space_id: str
    original_filename: str
    mime_type: str
    status: DocumentStatus = DocumentStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)
