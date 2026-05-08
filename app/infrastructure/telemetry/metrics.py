from __future__ import annotations

from prometheus_client import Counter, Histogram

# Stage 执行耗时（秒），按 stage 名称和租户分桶
STAGE_DURATION = Histogram(
    "arc_stage_duration_seconds",
    "Pipeline stage execution duration in seconds",
    ["stage", "tenant_id"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# Pipeline 总执行次数，按状态（success / error）和租户
PIPELINE_TOTAL = Counter(
    "arc_pipeline_total",
    "Total pipeline executions",
    ["status", "tenant_id"],
)

# LLM token 消耗总量，按租户 / 模型 / 方向（input | output）
LLM_TOKENS_TOTAL = Counter(
    "arc_llm_tokens_total",
    "Total LLM tokens consumed",
    ["tenant_id", "model", "type"],
)

# LLM 费用总量（美元），按租户 / 模型
LLM_COST_USD_TOTAL = Counter(
    "arc_llm_cost_usd_total",
    "Total LLM cost in USD",
    ["tenant_id", "model"],
)
