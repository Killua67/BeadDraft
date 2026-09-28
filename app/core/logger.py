"""
日志配置：控制台 + 按大小滚动的文件日志（logs/app.log），统一带时间戳。

uvicorn 自身的日志（启动信息、访问日志）也使用同一套格式，
见 run.py 中 uvicorn.run(log_config=build_log_config())。
"""

import logging
import logging.config

from app.core.config import settings

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def build_log_config() -> dict:
    """生成 logging.config.dictConfig 所需的配置字典。"""
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    handlers = ["console", "file"]
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"format": LOG_FORMAT, "datefmt": DATE_FORMAT},
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "default",
                "filename": str(settings.log_dir / "app.log"),
                "maxBytes": 10 * 1024 * 1024,
                "backupCount": 5,
                "encoding": "utf-8",
            },
        },
        "loggers": {
            "app": {"handlers": handlers, "level": settings.log_level, "propagate": False},
            "uvicorn": {"handlers": handlers, "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {"handlers": handlers, "level": "INFO", "propagate": False},
        },
    }


_configured = False


def setup_logging() -> None:
    """初始化日志（幂等，重复调用无副作用）。"""
    global _configured
    if _configured:
        return
    logging.config.dictConfig(build_log_config())
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """获取业务日志器，统一挂在 app.* 命名空间下。"""
    return logging.getLogger(name if name.startswith("app") else f"app.{name}")
