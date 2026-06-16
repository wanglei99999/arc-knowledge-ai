"""
pytest 全局 fixtures。

单元测试只需要 fake_ctx，不启动任何外部依赖。
集成测试在 tests/integration/conftest.py 里另外定义真实 DB fixtures。
"""
import os

# 单元测试隔离外部依赖：禁止 transformers 联网下载 tokenizer。
# 未缓存时 count_tokens 走字符估算降级路径（断言不依赖精确 token 数）。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import pytest

from app.pipeline.core.context import ProcessingContext, QuotaSnapshot, TenantConfig


@pytest.fixture
def tenant_config() -> TenantConfig:
    return TenantConfig(
        tenant_id="test-tenant",
        ingestion_strategy="standard",
        embedding_provider="openai_embedding",
        chunk_size=512,
        chunk_overlap=64,
    )


@pytest.fixture
def quota() -> QuotaSnapshot:
    return QuotaSnapshot(
        max_documents=1000,
        max_storage_bytes=10 * 1024 * 1024 * 1024,  # 10 GB
        max_api_calls_per_day=10000,
        used_documents=0,
        used_storage_bytes=0,
        used_api_calls_today=0,
    )


@pytest.fixture
def fake_ctx(tenant_config: TenantConfig, quota: QuotaSnapshot) -> ProcessingContext:
    return ProcessingContext.create(
        tenant_id="test-tenant",
        document_id="doc-001",
        quota=quota,
        config=tenant_config,
    )
