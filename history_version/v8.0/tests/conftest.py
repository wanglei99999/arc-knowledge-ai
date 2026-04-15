"""
pytest 全局 fixtures。

单元测试只需要 fake_ctx，不启动任何外部依赖。
集成测试在 tests/integration/conftest.py 里另外定义真实 DB fixtures。
"""
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
