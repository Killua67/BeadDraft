"""
启动脚本：uv run python run.py [--reload]

使用与业务代码相同的日志格式（带时间）启动 uvicorn。
监听地址和端口可通过环境变量 PB_HOST / PB_PORT 修改。
"""

import sys

import uvicorn

from app.core.config import settings
from app.core.logger import build_log_config

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload="--reload" in sys.argv,
        log_config=build_log_config(),
    )
