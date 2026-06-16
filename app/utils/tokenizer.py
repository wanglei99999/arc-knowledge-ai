from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 模块级缓存：tokenizer 实例，或加载失败后的 None。
# 注意：刻意不用 @lru_cache —— 它不缓存抛异常的调用，会导致
# tokenizer 加载失败时每次 count_tokens 都重新联网重试，拖死服务。
_tokenizer = None
_load_attempted = False


def _get_tokenizer():
    """加载 tokenizer，只尝试一次。失败后缓存 None，后续调用直接降级，不再重试。"""
    global _tokenizer, _load_attempted
    if _load_attempted:
        return _tokenizer

    _load_attempted = True
    try:
        from transformers import AutoTokenizer

        from app.config.settings import settings

        logger.info("Loading tokenizer: %s", settings.tokenizer_model)
        _tokenizer = AutoTokenizer.from_pretrained(
            settings.tokenizer_model,
            trust_remote_code=True,
        )
    except Exception:
        logger.warning("Tokenizer 加载失败，降级为字符估算（后续不再重试）", exc_info=True)
        _tokenizer = None
    return _tokenizer


def count_tokens(text: str) -> int:
    """计算文本的 token 数，优先使用 Qwen tokenizer 精确计算。

    首次调用会从 HuggingFace 加载 tokenizer 文件，后续从缓存复用。
    tokenizer 不可用时降级为字符估算，且加载只尝试一次，
    失败后所有调用走降级路径，避免重复联网重试拖慢服务。
    """
    tokenizer = _get_tokenizer()
    if tokenizer is None:
        return _char_estimate(text)
    try:
        return len(tokenizer.encode(text, add_special_tokens=False))
    except Exception:
        logger.warning("Tokenizer encode 失败，降级为字符估算", exc_info=True)
        return _char_estimate(text)


def _char_estimate(text: str) -> int:
    """字符比例法降级估算（Qwen BPE 经验系数）。"""
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    other = len(text) - cjk
    return max(1, int(cjk * 0.65 + other * 0.3))
