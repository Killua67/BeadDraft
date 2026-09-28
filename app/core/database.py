"""
数据库连接（SQLAlchemy 2.0 + SQLite）。

- Base：所有 ORM 模型的基类
- get_db：FastAPI 依赖，每个请求一个会话，请求结束自动关闭
- init_db：启动时建表（表已存在则跳过）
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)

settings.data_dir.mkdir(parents=True, exist_ok=True)

# check_same_thread=False：FastAPI 会在线程池中使用连接，SQLite 需要关闭同线程检查
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """ORM 模型基类。"""


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：提供数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """创建所有数据表。"""
    from app import models  # noqa: F401  确保模型已注册到 Base.metadata

    Base.metadata.create_all(bind=engine)
    logger.info("数据库初始化完成：%s", settings.database_url)
