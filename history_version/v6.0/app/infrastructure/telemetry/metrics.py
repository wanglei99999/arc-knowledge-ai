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
