"""
数据库迁移脚本：创建 ArcKnowledge 所需的 PostgreSQL 表结构。

用法：
    python scripts/migrate.py

幂等性：所有语句使用 CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS，
重复执行安全。
"""
from __future__ import annotations

import asyncio

import asyncpg

from app.config.settings import settings

DDL = """
-- 文档元数据表
CREATE TABLE IF NOT EXISTS documents (
    id              VARCHAR(64)  PRIMARY KEY,
    tenant_id       VARCHAR(64)  NOT NULL,
    space_id        VARCHAR(64)  NOT NULL,
    original_name   VARCHAR(512) NOT NULL,
    mime_type       VARCHAR(128) NOT NULL,
    file_path       VARCHAR(1024) NOT NULL,   -- MinIO object key
    status          VARCHAR(32)  NOT NULL DEFAULT 'pending',
    chunk_count     INTEGER      NOT NULL DEFAULT 0,
    error_message   TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_documents_tenant_space
    ON documents (tenant_id, space_id);

CREATE INDEX IF NOT EXISTS idx_documents_status
    ON documents (status);

-- 文档分片表（含向量元数据，向量本体存 Milvus）
CREATE TABLE IF NOT EXISTS document_chunks (
    chunk_id            VARCHAR(64)   PRIMARY KEY,
    document_id         VARCHAR(64)   NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id           VARCHAR(64)   NOT NULL,
    content             TEXT          NOT NULL,
    chunk_index         INTEGER       NOT NULL,
    token_count         INTEGER       NOT NULL DEFAULT 0,
    embedding_model     VARCHAR(128),
    embedding_status    VARCHAR(32)   NOT NULL DEFAULT 'pending',
    embedded_at         TIMESTAMPTZ,
    metadata            JSONB         NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chunks_document
    ON document_chunks (document_id);

CREATE INDEX IF NOT EXISTS idx_chunks_tenant
    ON document_chunks (tenant_id);

-- 入库任务日志
CREATE TABLE IF NOT EXISTS ingestion_logs (
    id              BIGSERIAL    PRIMARY KEY,
    document_id     VARCHAR(64)  NOT NULL,
    tenant_id       VARCHAR(64)  NOT NULL,
    activity        VARCHAR(64)  NOT NULL,   -- parse / chunk / embed_and_index
    status          VARCHAR(32)  NOT NULL,   -- started / completed / failed
    duration_ms     INTEGER,
    error           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_logs_document
    ON ingestion_logs (document_id);
"""


async def migrate() -> None:
    # asyncpg 直接用原始 PostgreSQL URL（不带 +asyncpg 驱动前缀）
    dsn = settings.postgres_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(DDL)
        print("Migration completed.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
