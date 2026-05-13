#!/usr/bin/env python
"""
一次性脚本：通过 HuggingFace 镜像站下载 BGE Reranker 模型到本地缓存。
下载完成后，正常启动应用（reranker_offline=true）即可使用本地模型。

用法：
    python scripts/download_bge_model.py

    # 指定其他镜像站（默认 hf-mirror.com）：
    HF_ENDPOINT=https://hf-mirror.com python scripts/download_bge_model.py
"""
from __future__ import annotations

import os
import sys

# 必须在 import transformers / FlagEmbedding 之前设置
_endpoint = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")
os.environ["HF_ENDPOINT"] = _endpoint

# 明确关闭离线模式，允许网络下载
os.environ.pop("TRANSFORMERS_OFFLINE", None)
os.environ.pop("TRANSFORMERS_OFFLINE_MODE", None)

_MODEL_NAME = "BAAI/bge-reranker-v2-m3"

print(f"[download] 使用镜像站: {_endpoint}")
print(f"[download] 下载模型: {_MODEL_NAME}")
print("[download] 首次下载约 1-2 GB，请耐心等待...\n")

try:
    from FlagEmbedding import FlagReranker  # type: ignore[import]
except ImportError:
    print("[error] FlagEmbedding 未安装，请先执行：pip install FlagEmbedding")
    sys.exit(1)

try:
    reranker = FlagReranker(_MODEL_NAME, use_fp16=True)
    scores = reranker.compute_score([["测试查询", "测试文档"]], normalize=True)
    print(f"\n[download] 模型下载并验证成功！测试得分: {scores}")
    print("[download] 现在可以正常启动应用，Reranker 将使用本地缓存。")
except Exception as e:
    print(f"\n[error] 下载或验证失败: {e}")
    print("[hint] 检查网络连接，或尝试: HF_ENDPOINT=https://hf-mirror.com python scripts/download_bge_model.py")
    sys.exit(1)
