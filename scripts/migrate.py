"""
数据库迁移脚本：创建 ArcKnowledge 所需的 PostgreSQL 表结构。

Schema 版本：v3（聊天附件）
相对旧版的变更：
  新增：messages 附件轮次处理状态与 client_request_id 幂等键
  新增：message_attachments 表（消息、附件占位与永久文档关系）
  新增：spaces 表（双标识 UUID id + space_key，settings JSONB）
  新增：message_citations 表（持久化 RAG 引用，含检索快照）
  新增：documents.file_size 字段
  新增：sessions.space_id 字段（FK to spaces.id，暂 NULLABLE，
        应用层适配完成后改为 NOT NULL）
  移除：ingestion_logs 表（由 P1 的 ingestion_jobs + ingestion_job_steps 替代）

用法：
    python scripts/migrate.py

幂等性：所有语句使用 CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS，
重复执行安全。

清盘重建（历史数据不保留时）：
    1. 停止 FastAPI 和 Temporal Worker
    2. 清空 Milvus collection（drop + recreate）
    3. 清空 Elasticsearch index（delete + recreate）
    4. DROP SCHEMA public CASCADE; CREATE SCHEMA public;
    5. python scripts/migrate.py
"""

from __future__ import annotations

import asyncio

import asyncpg

from app.config.settings import settings

# ── P0 Schema ────────────────────────────────────────────────────────────────
# 表创建顺序遵循外键依赖：
#   spaces → documents → document_chunks
#                      → sessions → messages → message_attachments
#                                           → message_citations
#                      → memories

DDL = """
-- ── 用户表 ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        VARCHAR(64)  NOT NULL,
    email            VARCHAR(255) NOT NULL,
    hashed_password  VARCHAR(255) NOT NULL,
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_users_tenant_email UNIQUE (tenant_id, email)
);

CREATE INDEX IF NOT EXISTS idx_users_tenant
    ON users (tenant_id);

-- ── 知识空间表 ───────────────────────────────────────────────────────────────
-- 双标识：id（UUID，内部主键）+ space_key（可读标识，对接旧数据 'default'）
-- space_key 在租户内唯一，不同租户可重名
CREATE TABLE IF NOT EXISTS spaces (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   VARCHAR(64)  NOT NULL,
    space_key   VARCHAR(128) NOT NULL,
    name        VARCHAR(256),
    description TEXT,
    status      VARCHAR(32)  NOT NULL DEFAULT 'active',
    -- active | archived | config_invalid
    settings    JSONB        NOT NULL DEFAULT '{}',
    -- 通过 SpaceSettings Pydantic 模型读写，禁止裸 dict 操作（见 ADR-034）
    created_by  VARCHAR(64),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_by  VARCHAR(64),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_spaces_tenant_key UNIQUE (tenant_id, space_key)
);

CREATE INDEX IF NOT EXISTS idx_spaces_tenant
    ON spaces (tenant_id);

-- ── 文档元数据表 ──────────────────────────────────────────────────────────────
-- space_id 使用 UUID FK，与 spaces.id 关联（见 ADR-035）
CREATE TABLE IF NOT EXISTS documents (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(64)  NOT NULL,
    space_id        UUID         NOT NULL REFERENCES spaces(id),
    original_name   VARCHAR(512) NOT NULL,
    mime_type       VARCHAR(128) NOT NULL,
    file_path       VARCHAR(1024) NOT NULL,  -- MinIO object key
    file_size       BIGINT,                  -- 文件字节数，上传时记录
    status          VARCHAR(32)  NOT NULL DEFAULT 'pending',
    chunk_count     INTEGER      NOT NULL DEFAULT 0,
    error_message   TEXT,
    created_by      VARCHAR(64),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_by      VARCHAR(64),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    deleted_by      VARCHAR(64),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_documents_tenant_space
    ON documents (tenant_id, space_id);

CREATE INDEX IF NOT EXISTS idx_documents_status
    ON documents (status);

CREATE INDEX IF NOT EXISTS idx_documents_active
    ON documents (tenant_id, space_id)
    WHERE deleted_at IS NULL;

-- ── 文档分片表 ────────────────────────────────────────────────────────────────
-- 仅存文本真相，向量本体在 Milvus，通过 chunk_id 关联
CREATE TABLE IF NOT EXISTS document_chunks (
    chunk_id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id      UUID         NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id        VARCHAR(64)  NOT NULL,
    content          TEXT         NOT NULL,
    chunk_index      INTEGER      NOT NULL,
    token_count      INTEGER      NOT NULL DEFAULT 0,
    metadata         JSONB        NOT NULL DEFAULT '{}',
    embedding_status VARCHAR(32)  NOT NULL DEFAULT 'pending',
    -- pending | current | stale
    embedded_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_chunk_doc_index UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_document
    ON document_chunks (document_id);

CREATE INDEX IF NOT EXISTS idx_chunks_tenant
    ON document_chunks (tenant_id);

-- ── 会话表 ────────────────────────────────────────────────────────────────────
-- space_id 暂 NULLABLE，应用层适配后设 NOT NULL（见 ADR-033）
CREATE TABLE IF NOT EXISTS sessions (
    session_id    UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     VARCHAR(64)  NOT NULL,
    user_id       VARCHAR(64)  NOT NULL,
    space_id      UUID         REFERENCES spaces(id),
    -- 创建后不可修改，对话归属空间唯一
    title         VARCHAR(256),
    summary       TEXT,
    -- Layer 2 情节记忆：中间段 LLM 压缩摘要
    summary_up_to UUID,
    -- 摘要截止到哪条 message_id
    message_count INTEGER      NOT NULL DEFAULT 0,
    archived_at   TIMESTAMPTZ,
    pinned_at     TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- CREATE TABLE IF NOT EXISTS 不会给旧表补列，因此保留显式增量迁移。
ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS pinned_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_sessions_user
    ON sessions (tenant_id, user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_sessions_space
    ON sessions (space_id);

CREATE INDEX IF NOT EXISTS idx_sessions_active_space
    ON sessions (tenant_id, user_id, space_id, updated_at DESC)
    WHERE archived_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_sessions_archived_user
    ON sessions (tenant_id, user_id, archived_at DESC)
    WHERE archived_at IS NOT NULL;

-- ── 消息表 ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS messages (
    message_id  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  UUID         NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    tenant_id   VARCHAR(64)  NOT NULL,
    user_id     VARCHAR(64)  NOT NULL,
    role        VARCHAR(16)  NOT NULL,
    -- user | assistant
    content     TEXT         NOT NULL,
    token_count INTEGER      NOT NULL DEFAULT 0,
    processing_status VARCHAR(32),
    processing_error  TEXT,
    client_request_id UUID,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- CREATE TABLE IF NOT EXISTS 不会给旧表补列，因此保留显式增量迁移。
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS processing_status VARCHAR(32);

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS processing_error TEXT;

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS client_request_id UUID;

CREATE INDEX IF NOT EXISTS idx_messages_session_time
    ON messages (session_id, created_at ASC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_client_request
    ON messages (tenant_id, user_id, client_request_id)
    WHERE client_request_id IS NOT NULL;

-- ── 消息附件关系表 ────────────────────────────────────────────────────────────
-- 上传状态只记录文件上传阶段；文档入库状态以 documents.status 为事实来源。
CREATE TABLE IF NOT EXISTS message_attachments (
    attachment_id UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id    UUID         NOT NULL REFERENCES messages(message_id) ON DELETE CASCADE,
    tenant_id     VARCHAR(64)  NOT NULL,
    space_id      UUID         NOT NULL REFERENCES spaces(id),
    client_id     VARCHAR(128) NOT NULL,
    document_id   UUID         REFERENCES documents(id),
    file_name     VARCHAR(512) NOT NULL,
    mime_type     VARCHAR(128) NOT NULL,
    file_size     BIGINT       NOT NULL,
    upload_status VARCHAR(32)  NOT NULL DEFAULT 'pending',
    -- pending | uploaded | failed
    upload_error  TEXT,
    ignored       BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_message_attachment_client UNIQUE (message_id, client_id)
);

CREATE INDEX IF NOT EXISTS idx_message_attachments_message
    ON message_attachments (message_id);

-- ── RAG 引用记录表 ─────────────────────────────────────────────────────────────
-- 持久化每次 RAG 回答引用的 chunk 来源，含快照字段保证历史可溯源
-- chunk / document 被删后仍可查看引用记录（通过快照字段）
CREATE TABLE IF NOT EXISTS message_citations (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id          UUID         NOT NULL REFERENCES messages(message_id) ON DELETE CASCADE,
    tenant_id           VARCHAR(64)  NOT NULL,
    space_id            UUID         REFERENCES spaces(id),
    document_id         UUID,
    -- 不加 FK，document 删除后记录仍保留
    document_version_id UUID,
    -- P1 document_versions 落地后填充
    chunk_id            UUID,
    -- 不加 FK，chunk 删除后记录仍保留
    rank                INTEGER,
    -- 在本次检索结果中的排名（1-based）
    score               FLOAT,
    -- RRF / 向量相似度分数
    retrieval_mode      VARCHAR(32),
    -- 'vector' | 'keyword' | 'hybrid'
    content_snapshot    TEXT         NOT NULL,
    -- chunk 原文快照，独立存在，不依赖 document_chunks
    title_snapshot      VARCHAR(512),
    -- 文档名快照
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_citations_message
    ON message_citations (message_id);

CREATE INDEX IF NOT EXISTS idx_citations_document
    ON message_citations (document_id);

-- ── 语义记忆表（Layer 3）──────────────────────────────────────────────────────
-- 向量本体存 Milvus arc_memories collection，此处存文本元数据
CREATE TABLE IF NOT EXISTS memories (
    memory_id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         VARCHAR(64)  NOT NULL,
    user_id           VARCHAR(64)  NOT NULL,
    category          VARCHAR(32)  NOT NULL,
    -- fact | preference | goal | context | entity
    content           TEXT         NOT NULL,
    source_session_id UUID         REFERENCES sessions(session_id) ON DELETE SET NULL,
    source_type       VARCHAR(32)  NOT NULL DEFAULT 'system',
    -- user | system | extractor | admin
    confidence        FLOAT        NOT NULL DEFAULT 1.0,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memories_user
    ON memories (tenant_id, user_id, updated_at DESC);

-- ── 租户 LLM 配置表 ──────────────────────────────────────────────────────────
-- 每租户独立配置 LLM 引擎和模型，空字符串 = 使用 settings 全局默认值
CREATE TABLE IF NOT EXISTS tenant_configs (
    tenant_id            VARCHAR(64)   PRIMARY KEY,
    default_llm_provider VARCHAR(64)   NOT NULL DEFAULT 'openai_llm',
    default_llm_model    VARCHAR(128)  NOT NULL DEFAULT '',
    allowed_models       JSONB         NOT NULL DEFAULT '[]',
    created_at           TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- ── 模型单价配置表 ────────────────────────────────────────────────────────────
-- 存储各 LLM 模型的 token 单价，写入 usage_records 时查询并快照到 cost_usd
CREATE TABLE IF NOT EXISTS model_configs (
    model_id           VARCHAR(128) PRIMARY KEY,
    input_cost_per_1k  NUMERIC(10,6) NOT NULL DEFAULT 0,
    output_cost_per_1k NUMERIC(10,6) NOT NULL DEFAULT 0,
    context_window     INT          NOT NULL DEFAULT 0,
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── LLM 用量记录表 ────────────────────────────────────────────────────────────
-- 每次 LLM 调用写入一条记录，cost_usd 在写入时按当时单价计算（历史快照）
CREATE TABLE IF NOT EXISTS usage_records (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     VARCHAR(64) NOT NULL,
    model         VARCHAR(128) NOT NULL,
    input_tokens  INT         NOT NULL DEFAULT 0,
    output_tokens INT         NOT NULL DEFAULT 0,
    cost_usd      NUMERIC(12,6) NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_usage_tenant_time
    ON usage_records (tenant_id, created_at DESC);

-- ── 默认空间 seed ─────────────────────────────────────────────────────────────
INSERT INTO spaces (tenant_id, space_key, name, status)
VALUES ('default', 'default', '默认空间', 'active')
ON CONFLICT (tenant_id, space_key) DO NOTHING;
"""

# ── P1 预留（下一阶段执行）───────────────────────────────────────────────────
# document_versions、document_files、ingestion_jobs、ingestion_job_steps
# SpaceSettings 解析框架（应用层，无 DDL）
# 详见 docs/design/db-evolution-v2.md § P1

# ── P2 预留 ──────────────────────────────────────────────────────────────────
# chunk_index_states、messages 增加 model_provider/model_name、audit_events
# 详见 docs/design/db-evolution-v2.md § P2


async def migrate() -> None:
    dsn = settings.postgres_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(DDL)
        print("Migration completed successfully.")
        print("Tables created: spaces, documents, document_chunks,")
        print("                sessions, messages, message_attachments,")
        print("                message_citations, memories,")
        print("                tenant_configs")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
