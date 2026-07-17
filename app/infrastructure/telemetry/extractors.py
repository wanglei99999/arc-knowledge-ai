from __future__ import annotations

from typing import Any, Protocol

from app.config.settings import settings
from app.domain.retrieval import SearchContext, SearchHit

# OpenInference 语义约定:span 类型键,Phoenix 靠它选择渲染面板
KIND_KEY = "openinference.span.kind"

_DOC_LIMIT = 10  # 每个 span 最多记录的文档条数,防属性爆炸


class ContentExtractor(Protocol):
    """把 Stage 输出翻译成 span 属性的翻译官协议。谁 matches 谁翻译。"""

    def matches(self, stage_name: str, payload: object) -> bool: ...

    def extract(self, stage_name: str, payload: object) -> dict[str, Any]: ...


def _doc_attrs(prefix: str, hits: list[SearchHit]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for i, h in enumerate(hits[:_DOC_LIMIT]):
        attrs[f"{prefix}.{i}.document.id"] = h.chunk_id
        attrs[f"{prefix}.{i}.document.score"] = h.score
    return attrs


class RetrievalExtractor:
    """检索域翻译官:认识 SearchContext / list[SearchHit],产出 OpenInference 属性。"""

    _KINDS = {
        "query_rewrite": "CHAIN",
        "vector_search": "RETRIEVER",
        "keyword_search": "RETRIEVER",
        "rrf_fusion": "CHAIN",
        "rerank": "RERANKER",
    }

    def matches(self, stage_name: str, payload: object) -> bool:
        return stage_name in self._KINDS and isinstance(payload, (SearchContext, list))

    def extract(self, stage_name: str, payload: object) -> dict[str, Any]:
        attrs: dict[str, Any] = {KIND_KEY: self._KINDS[stage_name]}
        capture = settings.otel_capture_content

        if isinstance(payload, SearchContext):
            q = payload.query
            if stage_name == "query_rewrite":
                if capture:
                    attrs["input.value"] = q.query_text
                    attrs["output.value"] = " | ".join(q.expanded_queries) or q.query_text
                attrs["arc.retrieval.intent_is_valid"] = q.intent_is_valid
            elif stage_name == "vector_search":
                if capture:
                    attrs["input.value"] = q.query_text
                attrs.update(_doc_attrs("retrieval.documents", payload.vector_hits))
            elif stage_name == "keyword_search":
                if capture:
                    attrs["input.value"] = q.query_text
                attrs.update(_doc_attrs("retrieval.documents", payload.keyword_hits))
            elif stage_name == "rrf_fusion":
                attrs["arc.fusion.hit_count"] = len(payload.ranked_hits)
                attrs.update(_doc_attrs("retrieval.documents", payload.ranked_hits))
        elif isinstance(payload, list) and stage_name == "rerank":
            hits = [h for h in payload if isinstance(h, SearchHit)]
            attrs.update(_doc_attrs("reranker.output_documents", hits))

        return attrs


# 模块级注册表:入库侧将来要收内容,在此 append 一个新 extractor 即可,Hook 不改
EXTRACTORS: list[ContentExtractor] = [RetrievalExtractor()]
