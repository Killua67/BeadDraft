"""
全局配置。

所有配置项都可以通过环境变量覆盖（前缀 PB_），本地开发不设置也能直接运行。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

# 项目根目录（app/core/config.py 往上三级）
BASE_DIR = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    return os.getenv(f"PB_{name}", default)


@dataclass(frozen=True)
class Settings:
    """应用配置（只读）。"""

    app_name: str = "拼豆图纸生成器"
    version: str = "0.1.0"

    # 服务监听地址与端口
    host: str = field(default_factory=lambda: _env("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("PORT", "8520")))

    # 日志级别：DEBUG / INFO / WARNING / ERROR
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO").upper())
    log_dir: Path = field(default_factory=lambda: Path(_env("LOG_DIR", str(BASE_DIR / "logs"))))

    # SQLite 数据库文件位置（运行时数据，不提交到 Git）
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(BASE_DIR / "data"))))

    # 内置色卡 JSON 目录、前端静态文件目录
    palette_dir: Path = BASE_DIR / "app" / "data" / "palettes"
    web_dir: Path = BASE_DIR / "web"

    # 上传图片大小上限（MB）、网格边长上限（格）
    max_upload_mb: int = field(default_factory=lambda: int(_env("MAX_UPLOAD_MB", "15")))
    max_grid_size: int = field(default_factory=lambda: int(_env("MAX_GRID_SIZE", "200")))

    # 渲染图纸用的字体（需支持中文），留空则自动在系统字体中查找
    font_path: str = field(default_factory=lambda: _env("FONT_PATH", ""))

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'perler.db'}"


settings = Settings()
