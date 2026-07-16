"""检索管线的 Hook 挂载测试。

两处脱节的回归:
1. HybridRetrievalStrategy.hooks 声明为空(欠账注释"Phase 3 开启")
2. RAGOrchestrator 用 build_pipeline() 而非 build_pipeline_with_hooks(),声明形同虚设
"""

from __future__ import annotations

# 装饰器注册在模块 import 时触发。不能 import app.main——_register_components()
# 在 lifespan(应用启动)里才跑,单纯 import 不注册,registry 会是空的。
import app.pipeline.stages.retrieval.keyword_search_stage  # noqa: F401
import app.pipeline.stages.retrieval.query_rewrite_stage  # noqa: F401
import app.pipeline.stages.retrieval.rerank_stage  # noqa: F401
import app.pipeline.stages.retrieval.rrf_fusion_stage  # noqa: F401
import app.pipeline.stages.retrieval.vector_search_stage  # noqa: F401
from app.pipeline.hooks.observability_hook import ObservabilityHook
from app.pipeline.strategies.retrieval.hybrid_strategy import HybridRetrievalStrategy


def test_hybrid_strategy_declares_observability_hook():
    """策略声明层:hooks 列表里必须有 ObservabilityHook(类,非实例)。"""
    assert ObservabilityHook in HybridRetrievalStrategy.hooks


def test_build_pipeline_with_hooks_attaches_hook(tenant_config):
    """组装层:build_pipeline_with_hooks 组出的管线真的带着 Hook 实例。"""
    strategy = HybridRetrievalStrategy()
    pipeline = strategy.build_pipeline_with_hooks("query", tenant_config)
    hook_types = [type(h) for h in pipeline._hook_runner.hooks]
    assert ObservabilityHook in hook_types
