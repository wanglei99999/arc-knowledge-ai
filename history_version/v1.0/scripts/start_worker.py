"""Temporal Worker 启动脚本"""
import asyncio
import sys
from pathlib import Path

# 将项目根目录加入 sys.path，无需安装包即可运行
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.infrastructure.temporal.worker import run_worker

if __name__ == "__main__":
    asyncio.run(run_worker())
