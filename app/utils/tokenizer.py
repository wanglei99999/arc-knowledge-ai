from __future__ import annotations

import logging
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_tokenizer():
    from transformers import AutoTokenizer
    from app.config.settings import settings

    logger.info("Loading tokenizer: %s", settings.tokenizer_model)
    return AutoTokenizer.from_pretrained(
        settings.tokenizer_model,
        trust_remote_code=True,
    )


def count_tokens(text: str) -> int:
    """精确计算文本的 token 数（基于 Qwen tokenizer）。

    首次调用会从 HuggingFace 下载 tokenizer 文件（约 7MB），后续从缓存加载。
    tokenizer 不可用时降级为字符估算，保证服务不中断。
    """
    try:
        tokenizer = _load_tokenizer()
        return len(tokenizer.encode(text, add_special_tokens=False))
    except Exception:
        logger.warning("Tokenizer unavailable, falling back to char estimate", exc_info=True)
        return _char_estimate(text)


def _char_estimate(text: str) -> int:
    """字符比例法降级估算（Qwen BPE 经验系数）。"""
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    other = len(text) - cjk
    return max(1, int(cjk * 0.65 + other * 0.3))
