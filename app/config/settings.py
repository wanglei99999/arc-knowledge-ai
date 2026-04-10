from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    应用配置，优先级：环境变量 > .env 文件 > 默认值
    生产环境通过 Nacos 覆盖动态配置（见 nacos_loader.py）
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── 应用 ──────────────────────────────────────────────────────────────────
    app_env: str = "development"
    log_level: str = "INFO"

    # ── PostgreSQL ────────────────────────────────────────────────────────────
    postgres_url: str = "postgresql+asyncpg://arc:arc@localhost:5432/arc_knowledge"

    # ── Milvus ────────────────────────────────────────────────────────────────
    milvus_host: str = "localhost"
    milvus_port: int = 19530

    # ── Elasticsearch ─────────────────────────────────────────────────────────
    es_url: str = "http://localhost:9200"

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── MinIO / S3 ────────────────────────────────────────────────────────────
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "arc-documents"
    minio_secure: bool = False

    # ── Temporal ──────────────────────────────────────────────────────────────
    temporal_host: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "arc-ingestion"

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_api_key: str = Field(default="", repr=False)
    openai_embedding_model: str = "text-embedding-3-small"
    openai_llm_model: str = "gpt-4o-mini"

    # ── Nacos（可选）──────────────────────────────────────────────────────────
    nacos_server: str = ""
    nacos_namespace: str = "dev"


# 全局单例，整个应用 import 这一个
settings = Settings()
