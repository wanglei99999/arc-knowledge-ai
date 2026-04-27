from __future__ import annotations

import json
import logging
from dataclasses import replace

from app.domain.retrieval import RetrievalQuery, SearchContext
from app.pipeline.core.context import ProcessingContext
from app.pipeline.core.registry import registry
from app.pipeline.core.stage import BaseStage
from app.providers.base import ChatMessage, LLMProvider

logger = logging.getLogger(__name__)

_REWRITE_PROMPT = """\
你是一个搜索查询优化助手。请分析用户的查询，完成以下两个任务：

1. 判断这是否是一个需要在知识库中检索信息的查询（非闲聊/问候/无意义输入）
2. 如果是有效的知识库查询，生成 3 个语义等价但表达不同的变体查询

严格按以下 JSON 格式输出，不要包含任何其他内容：
{
  "is_knowledge_query": true 或 false,
  "expanded_queries": ["变体1", "变体2", "变体3"]
}

若 is_knowledge_query 为 false，expanded_queries 返回空数组。

用户查询：{query_text}"""


@registry.stage("query_rewrite")
class QueryRewriteStage(BaseStage[SearchContext, SearchContext]):
    """
    查询改写 Stage。

    调用 LLM 完成两件事：
    1. 意图识别：判断是否为有效知识库查询，结果写入 query.intent_is_valid
    2. 多查询扩展：生成 3 个语义变体，写入 query.expanded_queries

    intent_is_valid=False 时，VectorSearchStage / KeywordSearchStage 会跳过检索，
    LLM 直接在无上下文的情况下回答（适用于问候、闲聊等）。

    任何错误（LLM 超时、JSON 解析失败等）均降级为 passthrough，不影响正常检索。
    """

    name = "query_rewrite"

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self._provider = provider

    def _get_provider(self, ctx: ProcessingContext) -> LLMProvider:
        if self._provider is not None:
            return self._provider
        from app.providers.llm.model_hub import model_hub
        return model_hub.get_provider(ctx)

    async def _execute(
        self,
        ctx: ProcessingContext,
        search_ctx: SearchContext,
    ) -> SearchContext:
        if not ctx.config.query_rewrite_enabled:
            return search_ctx

        try:
            provider = self._get_provider(ctx)
            prompt = _REWRITE_PROMPT.format(query_text=search_ctx.query.query_text)
            message = ChatMessage(role="user", content=prompt)
            raw = await provider.generate(ctx, [message])
            parsed = json.loads(raw.strip())

            is_valid: bool = bool(parsed.get("is_knowledge_query", True))
            expanded: list[str] = [
                q for q in parsed.get("expanded_queries", [])
                if isinstance(q, str) and q.strip()
            ]

            new_query = replace(
                search_ctx.query,
                intent_is_valid=is_valid,
                expanded_queries=expanded if is_valid else [],
            )
            return replace(search_ctx, query=new_query)

        except Exception:
            logger.warning(
                "QueryRewriteStage failed for query=%r, falling back to passthrough",
                search_ctx.query.query_text,
                exc_info=True,
            )
            return search_ctx
