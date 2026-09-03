#!/usr/bin/env python
"""
一次性脚本：下载 BGE Reranker 模型到本地缓存。
下载完成后，正常启动应用（reranker_offline=true）即可使用本地模型。

用法：
    # 直连 HuggingFace（默认，能访问 huggingface.co 时使用）
    python scripts/download_bge_model.py

    # 使用国内镜像站
    HF_ENDPOINT=https://hf-mirror.com python scripts/download_bge_model.py
"""

from __future__ import annotations

import os
import sys

# 明确关闭离线模式，允许网络下载
os.environ.pop("TRANSFORMERS_OFFLINE", None)
os.environ.pop("TRANSFORMERS_OFFLINE_MODE", None)

# 仅在用户显式指定时才设置 HF_ENDPOINT，否则使用 huggingface_hub 默认地址
_endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co")
if "HF_ENDPOINT" in os.environ:
    print(f"[download] 使用镜像站: {_endpoint}")
else:
    print(f"[download] 使用官方地址: {_endpoint}")

_MODEL_NAME = "BAAI/bge-reranker-v2-m3"

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
    print(
        "[hint] 检查网络连接；国内网络可尝试: "
        "HF_ENDPOINT=https://hf-mirror.com python scripts/download_bge_model.py"
    )
    sys.exit(1)
