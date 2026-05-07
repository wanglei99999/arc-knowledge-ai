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

    # ── 临时文件 ──────────────────────────────────────────────────────────────
    # 大文件解析时的临时目录，生产环境建议挂载独立卷（200MB+ 文件需要足够空间）
    temp_dir: str = "/tmp"

    # ── Temporal ──────────────────────────────────────────────────────────────
    temporal_host: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "arc-ingestion"

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = Field(default="", repr=False)
    openai_embedding_model: str = "text-embedding-3-small"
    openai_llm_model: str = "gpt-4o-mini"

    # ── Ollama（本地 LLM）────────────────────────────────────────────────────
    ollama_base_url: str = "http://localhost:11434"
    ollama_llm_model: str = "llama3.2"

    # ── JWT 认证 ──────────────────────────────────────────────────────────────
    jwt_secret: str = Field(default="change-me-in-production", repr=False)
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24   # 默认 24 小时

    # ── OpenTelemetry ─────────────────────────────────────────────────────────
    otel_enabled: bool = True
    otel_service_name: str = "arc-knowledge-ai"
    otlp_endpoint: str = "http://localhost:4317"  # Jaeger / Tempo gRPC 端点

    # ── ModelHub / 熔断降级 ───────────────────────────────────────────────────
    llm_fallback_provider: str = "ollama_llm"   # 主模型失败时切换的备用 Provider
    llm_max_retries: int = 3                    # tenacity 最大重试次数
    llm_retry_min_wait: float = 1.0             # 最小等待秒数
    llm_retry_max_wait: float = 10.0            # 最大等待秒数

    # ── 记忆系统（Phase 7）────────────────────────────────────────────────────
    memory_last_n: int = 10                        # 工作记忆保留的最近消息数
    memory_top_k: int = 5                          # 语义记忆检索 top-k
    memory_similarity_threshold: float = 0.6       # 语义记忆搜索最低相似度
    memory_update_threshold: float = 0.9           # >= 此相似度时更新而非新增
    ollama_context_window: int = 8192              # Ollama 模型上下文窗口大小

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 60   # 每个租户每分钟最大请求数

    # ── Semantic Cache ────────────────────────────────────────────────────────
    semantic_cache_enabled: bool = True
    semantic_cache_threshold: float = 0.90          # 余弦相似度命中阈值
    semantic_cache_ttl_seconds: int = 14400         # 4 小时基础 TTL
    semantic_cache_embedding_provider: str = "openai_embedding"

    # ── Nacos（可选）──────────────────────────────────────────────────────────
    nacos_server: str = ""
    nacos_namespace: str = "dev"


# 全局单例，整个应用 import 这一个
settings = Settings()
